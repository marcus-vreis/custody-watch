import numpy as np
import pytest

from custody_watch.video import VideoFrame, parse_result


class FakeResult:
    """Espelha o que o ultralytics entrega, sem carregar o ultralytics."""

    def __init__(self, boxes, names, image=None, ids=None):
        self._boxes = boxes
        self.names = names
        self.orig_img = image if image is not None else np.zeros((8, 8, 3), np.uint8)
        self._ids = ids

    @property
    def boxes(self):
        return _FakeBoxes(self._boxes, self._ids)


class _FakeBoxes:
    def __init__(self, itens, ids):
        self._itens = itens
        self.id = None if ids is None else np.array(ids, dtype=float)

    def __iter__(self):
        return iter(self._itens)

    def __len__(self):
        return len(self._itens)


class _Box:
    def __init__(self, cls_index, conf, xyxy):
        self.cls = np.array([float(cls_index)])
        self.conf = np.array([conf])
        self.xyxy = np.array([list(xyxy)])


NOMES = {0: "person", 28: "suitcase", 16: "dog"}


def test_converte_deteccao_rastreada():
    resultado = FakeResult([_Box(0, 0.9, (10, 20, 30, 60))], NOMES, ids=[7])

    tracked = parse_result(resultado, min_confidence=0.35)

    assert len(tracked) == 1
    assert tracked[0].track_id == 7
    assert tracked[0].cls == "person"
    assert tracked[0].bbox == (10.0, 20.0, 30.0, 60.0)


def test_descarta_classe_irrelevante():
    resultado = FakeResult(
        [_Box(0, 0.9, (0, 0, 1, 1)), _Box(16, 0.9, (0, 0, 1, 1))], NOMES, ids=[1, 2]
    )

    assert [d.cls for d in parse_result(resultado, 0.35)] == ["person"]


def test_descarta_baixa_confianca():
    resultado = FakeResult(
        [_Box(0, 0.9, (0, 0, 1, 1)), _Box(28, 0.1, (0, 0, 1, 1))], NOMES, ids=[1, 2]
    )

    assert [d.cls for d in parse_result(resultado, 0.35)] == ["person"]


def test_frame_sem_track_id_nao_produz_deteccao():
    """O tracker ainda nao atribuiu id: sem id nao ha o que acompanhar."""
    resultado = FakeResult([_Box(0, 0.9, (0, 0, 1, 1))], NOMES, ids=None)

    assert parse_result(resultado, 0.35) == []


def test_frame_vazio_nao_quebra():
    assert parse_result(FakeResult([], NOMES, ids=[]), 0.35) == []


def test_aparencia_e_calculada_so_para_pessoas():
    imagem = np.full((64, 64, 3), 120, np.uint8)
    resultado = FakeResult(
        [_Box(0, 0.9, (8, 8, 40, 56)), _Box(28, 0.9, (8, 8, 40, 56))],
        NOMES,
        image=imagem,
        ids=[1, 2],
    )

    tracked = parse_result(resultado, 0.35, with_appearance=True)

    por_classe = {d.cls: d for d in tracked}
    assert por_classe["person"].appearance is not None
    assert por_classe["suitcase"].appearance is None


def test_sem_aparencia_pedida_o_campo_fica_vazio():
    imagem = np.full((64, 64, 3), 120, np.uint8)
    resultado = FakeResult([_Box(0, 0.9, (8, 8, 40, 56))], NOMES, image=imagem, ids=[1])

    assert parse_result(resultado, 0.35, with_appearance=False)[0].appearance is None


def test_video_frame_carrega_os_tres():
    quadro = VideoFrame(t=1.5, image=np.zeros((4, 4, 3), np.uint8), tracked=[])

    assert quadro.t == 1.5
    assert quadro.image.shape == (4, 4, 3)
    assert quadro.tracked == []


@pytest.mark.skipif(
    not __import__("pathlib").Path("data/caviar/LeftBag/LeftBag.mpg").exists(),
    reason="exige o dataset baixado; o CI nao tem",
)
def test_integracao_le_video_real():
    """Roda o detector de verdade. Pulado no CI, que nao tem dataset nem pesos."""
    from pathlib import Path

    from custody_watch.video import VideoSource

    fonte = VideoSource(Path("data/caviar/LeftBag/LeftBag.mpg"))
    primeiro = next(iter(fonte))

    assert primeiro.t >= 0.0
    assert primeiro.image.ndim == 3


# --- quadros crus, sem detector -----------------------------------------------


def _grava_video(destino, n=10, fps=25.0, largura=32, altura=24):
    """Um vídeo minúsculo de verdade, para exercitar a leitura sem o dataset."""
    import cv2
    import numpy as np

    escritor = cv2.VideoWriter(
        str(destino), cv2.VideoWriter_fourcc(*"MJPG"), fps, (largura, altura)
    )
    for i in range(n):
        quadro = np.full((altura, largura, 3), i * 10, dtype=np.uint8)
        escritor.write(quadro)
    escritor.release()
    return destino


def test_quadros_crus_devolvem_imagem_e_instante(tmp_path):
    """O recorte de clipe precisa da imagem, não de detecção nova: as caixas
    vêm da passagem que a sessão já fez. Rodar o detector outra vez custaria o
    dobro para produzir exatamente as mesmas caixas."""
    from custody_watch.video import raw_frames

    caminho = _grava_video(tmp_path / "curto.avi", n=10, fps=25.0)

    lidos = list(raw_frames(caminho))

    assert len(lidos) == 10
    assert lidos[0][0] == 0.0
    assert lidos[4][0] == pytest.approx(4 / 25.0)
    assert lidos[0][1].shape == (24, 32, 3)


def test_quadros_crus_aceitam_fps_declarado(tmp_path):
    """Um arquivo cujo fps o container não declara direito não pode inventar
    a grade de tempo: quem chama passa o número que já leu."""
    from custody_watch.video import raw_frames

    caminho = _grava_video(tmp_path / "curto.avi", n=6, fps=25.0)

    lidos = list(raw_frames(caminho, fps=10.0))

    assert lidos[2][0] == pytest.approx(0.2)


def test_caixa_que_toca_a_borda_de_baixo_e_marcada():
    """Só quem produz a detecção sabe o tamanho do quadro. A partir daqui o
    pé da caixa é tratado como o ponto no chão, e sem esta marca uma caixa
    cortada pela borda seria projetada como se estivesse apoiada ali."""
    imagem = np.zeros((100, 200, 3), np.uint8)
    resultado = FakeResult(
        [_Box(28, 0.9, (10, 60, 50, 100)), _Box(28, 0.9, (80, 40, 120, 80))],
        NOMES,
        image=imagem,
        ids=[1, 2],
    )

    cortada, inteira = parse_result(resultado, min_confidence=0.35)

    assert cortada.touches_bottom is True
    assert inteira.touches_bottom is False
