"use strict";

class ContactReviewUI {
  constructor(registrySha256) {
    this.$ = id => document.getElementById(id);
    this.store = /^[a-f0-9]{64}$/.test(registrySha256 || "") ? new ContactReview.ReviewStore(registrySha256) : null;
    this.context = null; this.image = null; this.size = null; this.overlay = null; this.exportSerial = 0;
    this.onImageClick = event => this.mark(event);
    this.$("contact-id").addEventListener("change", () => this.select());
    this.$("contact-status").addEventListener("change", () => {
      this.$("contact-x").value = ""; this.$("contact-y").value = "";
      this.sync(); this.draw();
    });
    this.$("contact-provenance").addEventListener("change", () => {
      this.$("contact-human").checked = false; this.sync();
    });
    for (const id of ["contact-x", "contact-y"]) this.$(id).addEventListener("input", () => this.draw());
    this.$("contact-form").addEventListener("submit", event => { event.preventDefault(); this.save(); });
    this.$("contact-remove").addEventListener("click", () => this.remove());
    for (const id of ["contact-export", "contact-export-modal"]) this.$(id).addEventListener("click", () => this.download());
    this.updateExport(); this.sync();
  }

  id() {
    const value = this.$("contact-id").value;
    return value === "" ? null : Number(value);
  }
  feedback(message, error = false) {
    const node = this.$("contact-message");
    node.textContent = message; node.className = error ? "contact-feedback error" : "contact-feedback";
  }
  resetFields() {
    this.$("contact-status").value = "unknown";
    this.$("contact-x").value = ""; this.$("contact-y").value = "";
    this.$("contact-provenance").value = ""; this.$("contact-method").value = "";
    this.$("contact-human").checked = false;
  }
  close() {
    if (this.image) this.image.removeEventListener("click", this.onImageClick);
    if (this.overlay) this.overlay.remove();
    this.context = null; this.image = null; this.size = null; this.overlay = null;
    this.resetFields(); this.sync();
  }
  open(config, camera, snapshot, image) {
    this.close();
    this.context = ContactReview.createReviewContext(config, camera, snapshot);
    this.image = image;
    const select = this.$("contact-id"), placeholder = document.createElement("option");
    placeholder.value = ""; placeholder.textContent = "Escolha um ID deste quadro";
    select.replaceChildren(placeholder);
    const classes = {person:"Pessoa", suitcase:"Mala", backpack:"Mochila", handbag:"Bolsa"};
    for (const track of this.context?.tracks || []) {
      const option = document.createElement("option"); option.value = String(track.local_track_id);
      option.textContent = `#${track.local_track_id} · ${classes[track.cls] || track.cls}`; select.append(option);
    }
    select.value = "";
    this.$("contact-details").textContent = "Selecione um ID para ver caixa e projeção diagnóstica.";
    this.$("contact-domain").textContent = "";
    this.$("contact-binding").textContent = this.context
      ? `Ambiente: ${this.context.registry_sha256}\nSequência: ${this.context.sequence_sha256}\n${this.context.camera_id} · ${this.context.segment_id} · quadro ${this.context.frame_index}`
      : "Vínculo indisponível.";
    if (this.context) this.feedback("Carregando a imagem antes de habilitar a anotação…");
    else this.feedback(snapshot.image?.kind === "schematic"
      ? "Esquema sintético: inspeção apenas; ele não define pixels da câmera para anotar."
      : "Este quadro não tem vínculo completo com o ambiente atual. Recarregue a página.");
    image.addEventListener("click", this.onImageClick);
    this.sync();
  }
  ready(width, height) {
    if (!this.context) return;
    if (![width, height].every(value => Number.isSafeInteger(value) && value > 0)) { this.unavailable(); return; }
    this.size = [width, height];
    this.overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.overlay.classList.add("contact-overlay");
    this.overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.overlay.setAttribute("width", String(width)); this.overlay.setAttribute("height", String(height));
    this.overlay.setAttribute("aria-hidden", "true"); this.$("inspection-image").append(this.overlay);
    this.$("contact-domain").textContent = `Imagem original: ${width} × ${height} px · 0 ≤ x < ${width}; 0 ≤ y < ${height}`;
    this.feedback(this.context.tracks.length ? "Escolha um ID. Pontos ainda não guardados são descartados ao trocar de ID ou fechar." : "Este quadro de imagem não contém IDs; os últimos IDs de outros quadros não são reutilizados.");
    this.zoom(); this.sync();
  }
  unavailable() {
    this.size = null; this.feedback("Imagem indisponível; anotação desabilitada.", true); this.sync();
  }
  select() {
    this.resetFields();
    const track = this.context?.tracks.find(item => item.local_track_id === this.id());
    const reasons = {ground_contact_unknown:"Contato no chão desconhecido", image_border:"Caixa toca a borda inferior", no_common_frame:"Sem referencial comum"};
    this.$("contact-details").textContent = track
      ? `ID local #${track.local_track_id} · bbox ${JSON.stringify(track.bbox)} px · projeção da bbox ${track.projected_position_m === null ? "indisponível" : `${JSON.stringify(track.projected_position_m)} m`} · ${reasons[track.position_status] || "Posição não qualificada"}.`
      : "Selecione um ID para ver caixa e projeção diagnóstica.";
    const saved = this.context && this.store?.get(this.context, this.id());
    if (saved) {
      this.$("contact-status").value = saved.status;
      if (saved.point_px) { this.$("contact-x").value = String(saved.point_px[0]); this.$("contact-y").value = String(saved.point_px[1]); }
      this.$("contact-provenance").value = saved.provenance; this.$("contact-method").value = saved.method;
      this.$("contact-human").checked = saved.human_confirmed;
    }
    this.feedback(saved ? "Anotação já guardada. Remova-a explicitamente para corrigir; arquivos exportados permanecem intactos." : "Selecione o estado, a origem e o método. Para contato observado, clique na imagem ou informe x e y.");
    this.sync(); this.draw();
  }
  point() {
    const values = [this.$("contact-x").value, this.$("contact-y").value];
    if (values.some(value => value.trim() === "")) return null;
    const point = values.map(Number);
    return point.every(Number.isFinite) ? point : null;
  }
  sync() {
    const ready = this.store && this.context && this.size && this.context.tracks.length;
    this.$("contact-fields").disabled = !ready;
    const selected = ready && this.id() !== null;
    const saved = selected && this.store.get(this.context, this.id());
    const editable = selected && !saved;
    for (const id of ["contact-status", "contact-provenance", "contact-method", "contact-human"]) this.$(id).disabled = !editable;
    const observed = editable && this.$("contact-status").value === "observed";
    this.$("contact-x").disabled = !observed; this.$("contact-y").disabled = !observed;
    this.$("contact-human-label").hidden = this.$("contact-provenance").value !== "human_review";
    this.$("contact-save").disabled = !editable; this.$("contact-remove").disabled = !saved;
    if (this.image) this.image.style.cursor = observed ? "crosshair" : "default";
  }
  mark(event) {
    if (!this.context || !this.size || this.id() === null || this.store?.get(this.context, this.id()) || this.$("contact-status").value !== "observed") return;
    const point = ContactReview.sourcePoint(event.clientX, event.clientY, this.image.getBoundingClientRect(), this.size);
    if (!point) { this.feedback("Clique dentro da imagem original.", true); return; }
    this.$("contact-x").value = String(point[0]); this.$("contact-y").value = String(point[1]); this.draw();
  }
  zoom() { if (this.overlay && this.image) this.overlay.style.width = this.image.style.width || "100%"; }
  draw() {
    if (!this.overlay) return;
    this.overlay.replaceChildren();
    const append = (tag, attrs) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
      this.overlay.append(node);
    };
    const track = this.context?.tracks.find(item => item.local_track_id === this.id());
    if (!track) return;
    const [x1,y1,x2,y2] = track.bbox;
    append("rect", {x:x1,y:y1,width:x2-x1,height:y2-y1,fill:"none",stroke:"#ffd28a","stroke-width":2,"vector-effect":"non-scaling-stroke"});
    const point = this.$("contact-status").value === "observed" ? this.point() : null;
    if (!point) return;
    const [x,y] = point;
    append("circle", {cx:x,cy:y,r:7,fill:"#ffd28a",stroke:"#111a25","stroke-width":2,"vector-effect":"non-scaling-stroke"});
    append("path", {d:`M ${x-14} ${y} h 28 M ${x} ${y-14} v 28`,stroke:"#ffd28a","stroke-width":2,"vector-effect":"non-scaling-stroke"});
  }
  save() {
    try {
      if (!this.context || !this.size || !this.store) throw new Error("Aguarde uma imagem vinculada ao ambiente.");
      this.store.add(ContactReview.createDraft(this.context, this.size, this.id(), {
        status:this.$("contact-status").value, point_px:this.point(),
        provenance:this.$("contact-provenance").value, method:this.$("contact-method").value,
        human_confirmed:this.$("contact-human").checked,
      }));
      this.updateExport(); this.sync();
      this.feedback("Anotação guardada nesta página. Exporte o rascunho para manter um arquivo.");
    } catch (error) { this.feedback(error.message, true); }
  }
  remove() {
    if (!this.context || !this.store?.remove(this.context, this.id())) return;
    this.resetFields(); this.updateExport(); this.sync(); this.draw();
    this.feedback("Anotação removida deste rascunho. Arquivos já exportados permanecem intactos.");
  }
  updateExport() {
    const count = this.store?.size || 0;
    this.$("contact-count").textContent = count === 1 ? "1 anotação guardada nesta página" : `${count} anotações guardadas nesta página`;
    for (const id of ["contact-export", "contact-export-modal"]) this.$(id).disabled = count === 0;
    this.$("contact-export-preview").textContent = this.store ? JSON.stringify(this.store.export(), null, 2) : "Ambiente sem vínculo para exportação.";
  }
  download() {
    try {
      if (!this.store?.size) throw new Error("Guarde ao menos uma anotação antes de exportar.");
      const text = JSON.stringify(this.store.export(), null, 2) + "\n";
      const url = URL.createObjectURL(new Blob([text], {type:"application/json;charset=utf-8"}));
      const link = document.createElement("a");
      link.href = url; link.download = `ground-contacts-${Date.now()}-${++this.exportSerial}.json`; link.hidden = true;
      const dialog = this.$("inspector"); (dialog.open ? dialog : document.body).append(link);
      link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
      const message = "Arquivo preparado. Confira o download no navegador; a prévia JSON também pode ser copiada. O rascunho permanece nesta página.";
      this.$("contact-global-message").textContent = message; this.feedback(message);
    } catch (error) { this.$("contact-global-message").textContent = error.message; this.feedback(error.message, true); }
  }
}
