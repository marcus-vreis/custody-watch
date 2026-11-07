"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {createReviewContext, sourcePoint, createDraft, contactsExport, ReviewStore} = require("../src/custody_watch/assets/contact_review.js");

const registry = "a".repeat(64), sequence = "b".repeat(64);
function data() {
  const track = id => ({
    track_key: {camera_id:"cam-a", segment_id:"seg-1", local_track_id:id}, cls:"person",
    bbox:[100, 0, 200, 200], projected_position_m:[1.5, 2], position_m:null,
    position_status:"ground_contact_unknown", association_eligible:false,
  });
  return {
    config:{registry_sha256:registry},
    camera:{camera_id:"cam-a", segment_id:"seg-1", sequence_sha256:sequence, source_kind:"video", mode:"oracle", provenance:"ground_truth"},
    snapshot:{camera_id:"cam-a", segment_id:"seg-1", tracks:[track(777)], last_tracks:[track(888)], image:{
      frame_index:0, t:0, media_t:0, age_s:2, historical:true, kind:"video",
      registry_sha256:registry, sequence_sha256:sequence, tracks:[track(1), track(9)],
    }},
  };
}
function context() { const d=data(); return createReviewContext(d.config,d.camera,d.snapshot); }
const input = (change={}) => ({status:"observed", point_px:[150,200], provenance:"model_prediction", method:"Estimativa visual de teste.", ...change});
const draft = (change={}) => createDraft(context(), [400,240], 1, input(change));

test("catálogo vem da imagem histórica e fica congelado", () => {
  const d=data(), ctx=createReviewContext(d.config,d.camera,d.snapshot);
  assert.deepEqual(ctx.tracks.map(t=>t.local_track_id),[1,9]);
  assert.equal(ctx.frame_index,0); assert.equal(ctx.historical,true);
  d.snapshot.image.tracks[0].bbox[0]=999;
  assert.deepEqual(ctx.tracks[0].bbox,[100,0,200,200]);
  assert.equal(Object.isFrozen(ctx.tracks[0].bbox),true);
});
test("imagem vazia não empresta último ID de outro quadro", () => {
  const d=data(); d.snapshot.image.tracks=[];
  const ctx=createReviewContext(d.config,d.camera,d.snapshot);
  assert.deepEqual(ctx.tracks,[]);
  assert.throws(()=>createDraft(ctx,[400,240],888,input()),/ID/);
});
test("hashes e segmento incompatíveis recusam contexto", () => {
  for(const change of [d=>d.config.registry_sha256="c".repeat(64),d=>d.camera.sequence_sha256="c".repeat(64),d=>d.snapshot.segment_id="outro",d=>delete d.snapshot.image.sequence_sha256]) {
    const d=data(); change(d); assert.equal(createReviewContext(d.config,d.camera,d.snapshot),null);
  }
});
test("esquema não se converte em domínio de pixels da câmera", () => {
  const d=data(); d.snapshot.image.kind="schematic";
  assert.equal(createReviewContext(d.config,d.camera,d.snapshot),null);
});
test("namespace divergente, ID duplicado ou inseguro não compõem catálogo", () => {
  for(const change of [d=>d.snapshot.image.tracks[0].track_key.camera_id="cam-b",d=>d.snapshot.image.tracks.push(d.snapshot.image.tracks[0]),d=>d.snapshot.image.tracks[0].track_key.local_track_id=2**53]) {
    const d=data(); change(d); assert.equal(createReviewContext(d.config,d.camera,d.snapshot),null);
  }
});
test("zoom e rolagem são convertidos pelo retângulo efetivamente exibido", () => {
  assert.deepEqual(sourcePoint(200,150,{left:100,top:100,width:400,height:240},[800,480]),[200,100]);
  assert.deepEqual(sourcePoint(180,100,{left:-420,top:-20,width:960,height:540},[1920,1080]),[1200,240]);
  assert.deepEqual(sourcePoint(0,0,{left:0,top:0,width:800,height:480},[800,480]),[0,0]);
});
test("clique na borda externa ou fora da imagem não é ajustado silenciosamente", () => {
  for(const [x,y] of [[800,10],[10,480],[-1,10],[10,-1],[NaN,10]])
    assert.equal(sourcePoint(x,y,{left:0,top:0,width:800,height:480},[800,480]),null);
  assert.equal(sourcePoint(0,0,{left:0,top:0,width:0,height:480},[800,480]),null);
});
test("rascunho não herda proveniência oracle e exige origem explícita", () => {
  assert.equal(draft().provenance,"model_prediction");
  assert.throws(()=>draft({provenance:""}),/origem/);
  assert.throws(()=>draft({provenance:"ground_truth"}),/origem/);
  assert.throws(()=>draft({provenance:"human_review"}),/pessoal/);
  assert.equal(draft({provenance:"human_review",human_confirmed:true}).provenance,"human_review");
});
test("contato observado exige ponto finito e método; outros estados exigem null", () => {
  for(const point of [null,[NaN,0],[true,0],[400,0],[0,240],[-1,0]]) assert.throws(()=>draft({point_px:point}),/ponto|Ponto/);
  assert.throws(()=>draft({method:" "}),/método/);
  assert.throws(()=>draft({status:"occluded"}),/ponto|Ponto/);
  assert.equal(draft({status:"occluded",point_px:null}).point_px,null);
  assert.equal(draft({status:"unknown",point_px:null}).status,"unknown");
  assert.throws(()=>createDraft(context(),[400,240],777,input()),/ID/);
});
test("arquivo v1 contém somente campos do contrato e ordena observações", () => {
  const ctx=context();
  const a=createDraft(ctx,[400,240],9,input({status:"unknown",point_px:null}));
  const b=draft(); const result=contactsExport(registry,[a,b]);
  assert.deepEqual(Object.keys(result).sort(),["cameras","registry_sha256","schema_version"]);
  assert.equal(result.schema_version,1);
  assert.deepEqual(result.cameras[0].observations.map(o=>o.local_track_id),[1,9]);
  assert.deepEqual(Object.keys(result.cameras[0].observations[0]).sort(),["frame_index","local_track_id","method","point_px","provenance","status"]);
  assert.equal("position_m" in result.cameras[0].observations[0],false);
});
test("outro ambiente ou dimensões incompatíveis não são misturados no arquivo", () => {
  assert.throws(()=>contactsExport("c".repeat(64),[draft()]),/ambiente/);
  const d=data(); d.snapshot.image.frame_index=1;
  const other=createDraft(createReviewContext(d.config,d.camera,d.snapshot),[800,480],9,input());
  assert.throws(()=>contactsExport(registry,[draft(),other]),/Dimensões/);
});
test("guardar duplicata exige remoção explícita e nunca substitui origem", () => {
  const store=new ReviewStore(registry); store.add(draft());
  assert.throws(()=>store.add(draft({provenance:"human_review",human_confirmed:true})),/Remova/);
  assert.equal(store.export().cameras[0].observations[0].provenance,"model_prediction");
  assert.equal(store.remove(context(),1),true); assert.equal(store.size,0);
  store.add(draft({status:"unknown",point_px:null})); assert.equal(store.size,1);
});
test("limite do rascunho falha antes de mutar a coleção", () => {
  const store=new ReviewStore(registry,1); store.add(draft());
  const second=createDraft(context(),[400,240],9,input());
  assert.throws(()=>store.add(second),/Limite/);
  assert.equal(store.size,1); assert.equal(store.get(context(),9),null);
});

test("outro ambiente não consulta nem remove uma anotação com os mesmos IDs", () => {
  const store=new ReviewStore(registry); store.add(draft());
  const other={...context(),registry_sha256:"c".repeat(64)};
  assert.equal(store.get(other,1),null);
  assert.equal(store.remove(other,1),false);
  assert.equal(store.size,1);
});
test("alterar o arquivo exportado não modifica o rascunho guardado", () => {
  const store=new ReviewStore(registry); store.add(draft());
  const file=store.export(); file.cameras[0].observations[0].point_px[0]=300;
  assert.deepEqual(store.get(context(),1).point_px,[150,200]);
});
