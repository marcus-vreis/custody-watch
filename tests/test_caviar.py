import pytest

from custody_watch.caviar import (
    BAG_ID_OFFSET,
    PERSON_HEIGHT_M,
    estimate_metres_per_pixel,
    ground_plane,
    load_clip,
)
from custody_watch.types import Point

XML_MINIMO = """<dataset>
  <frame number="0">
    <objectlist>
      <object id="1"><box h="34" w="14" xc="100" yc="50"/><hypothesislist>
        <hypothesis><role>walker</role></hypothesis></hypothesislist></object>
    </objectlist>
  </frame>
  <frame number="25">
    <objectlist>
      <object id="1"><box h="30" w="14" xc="120" yc="50"/><hypothesislist>
        <hypothesis><role>walker</role></hypothesis></hypothesislist></object>
      <object id="4"><box h="14" w="18" xc="200" yc="60"/><hypothesislist>
        <hypothesis><role>leaving object</role></hypothesis></hypothesislist></object>
    </objectlist>
  </frame>
</dataset>
"""


@pytest.fixture
def clipe(tmp_path):
    """Um cenário mínimo no formato do CAVIAR, sem depender do download."""
    destino = tmp_path / "LeftBag"
    destino.mkdir()
    (destino / "lb1gt.xml").write_text(XML_MINIMO, encoding="utf-8")
    return tmp_path


def test_papel_leaving_object_vira_bagagem(clipe):
    frames = dict(load_clip(clipe, "LeftBag"))
    classes = {d.track_id: d.cls for d in frames[1.0]}

    assert classes[1] == "person"
    assert classes[4 + BAG_ID_OFFSET] == "suitcase"


def test_bagagem_recebe_espaco_de_ids_deslocado(clipe):
    """Ids do CAVIAR se repetem entre pessoas e bagagens no mesmo clipe."""
    frames = dict(load_clip(clipe, "LeftBag"))

    assert {d.track_id for d in frames[1.0]} == {1, 1004}


def test_numero_do_frame_vira_segundos(clipe):
    tempos = [t for t, _ in load_clip(clipe, "LeftBag")]

    assert tempos == [0.0, 1.0]


def test_bbox_sai_do_centro_e_dimensoes(clipe):
    frames = dict(load_clip(clipe, "LeftBag"))
    pessoa = next(d for d in frames[0.0] if d.track_id == 1)

    assert pessoa.bbox == (93.0, 33.0, 107.0, 67.0)


def test_cenario_desconhecido_e_rejeitado(clipe):
    with pytest.raises(ValueError, match="cenário desconhecido"):
        list(load_clip(clipe, "NaoExiste"))


def test_escala_vem_da_altura_media_de_pessoa(clipe):
    """Alturas 34 e 30 dão média 32; a bagagem não entra na conta."""
    assert estimate_metres_per_pixel(clipe) == pytest.approx(PERSON_HEIGHT_M / 32.0)


def test_sem_pessoa_anotada_falha_alto(tmp_path):
    destino = tmp_path / "LeftBag"
    destino.mkdir()
    (destino / "lb1gt.xml").write_text("<dataset></dataset>", encoding="utf-8")

    with pytest.raises(ValueError, match="nenhuma caixa de pessoa"):
        estimate_metres_per_pixel(tmp_path)


def test_download_parcial_nao_derruba_a_estimativa(clipe):
    """Tres dos quatro clipes chegam a mesma escala; faltar um nao e erro."""
    assert estimate_metres_per_pixel(clipe) > 0.0


def test_ground_plane_aplica_a_escala():
    plano = ground_plane(0.05)

    assert plano.project(100.0, 200.0) == Point(5.0, 10.0)
