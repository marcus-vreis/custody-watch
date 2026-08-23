import numpy as np
import pytest
from PIL import Image

from custody_watch.clips import ClipRequest, render_clip
from custody_watch.tracking import TrackedDetection


def quadro(valor: int = 40) -> np.ndarray:
    return np.full((32, 48, 3), valor, dtype=np.uint8)


def caixa(track_id: int, cls: str = "person") -> TrackedDetection:
    return TrackedDetection(track_id=track_id, cls=cls, bbox=(4.0, 4.0, 14.0, 24.0))


def cena(n: int = 30, fps: float = 25.0):
    """Quadros variam de brilho: idênticos seriam colapsados pelo otimizador do
    GIF, e vídeo real nunca repete um quadro exatamente."""
    return (
        (i / fps, quadro(20 + (7 * i) % 200), [caixa(1), caixa(1001, "suitcase")]) for i in range(n)
    )


def pedido(tmp_path, inicio=0.0, fim=1.0, pessoas=(1,), bag=1001) -> ClipRequest:
    return ClipRequest(
        start_s=inicio,
        end_s=fim,
        person_ids=frozenset(pessoas),
        bag_id=bag,
        output=tmp_path / "clipe.gif",
    )


def test_grava_gif_na_janela_pedida(tmp_path):
    destino = render_clip(cena(), pedido(tmp_path, 0.0, 1.0), output_fps=5.0)

    assert destino is not None
    assert destino.exists()
    with Image.open(destino) as gif:
        assert gif.format == "GIF"
        # Janela inclusiva: 0.0, 0.2, 0.4, 0.6, 0.8 e 1.0.
        assert gif.n_frames == 6


def test_taxa_de_saida_reduz_o_numero_de_quadros(tmp_path):
    """Vinte segundos a 25 fps não cabem numa página; a 5 fps cabem."""
    cheio = render_clip(cena(50), pedido(tmp_path, 0.0, 2.0), output_fps=25.0)
    with Image.open(cheio) as gif:
        muitos = gif.n_frames

    enxuto = render_clip(cena(50), pedido(tmp_path, 0.0, 2.0), output_fps=5.0)
    with Image.open(enxuto) as gif:
        poucos = gif.n_frames

    assert muitos > poucos


def test_quadros_fora_da_janela_sao_descartados(tmp_path):
    destino = render_clip(cena(50), pedido(tmp_path, 1.0, 1.4), output_fps=5.0)

    with Image.open(destino) as gif:
        assert gif.n_frames == 3


def test_janela_vazia_devolve_none(tmp_path):
    """Alerta perto do fim do vídeo não é erro — é janela sem quadro."""
    assert render_clip(cena(10), pedido(tmp_path, 100.0, 110.0)) is None


def test_janela_invertida_e_rejeitada(tmp_path):
    with pytest.raises(ValueError, match="janela invertida"):
        render_clip(cena(), pedido(tmp_path, 5.0, 1.0))


def test_escala_amplia_a_saida(tmp_path):
    destino = render_clip(cena(), pedido(tmp_path, 0.0, 0.4), scale=3)

    with Image.open(destino) as gif:
        assert gif.size == (48 * 3, 32 * 3)


def test_destaque_pinta_a_pessoa_de_forma_diferente_da_neutra(tmp_path):
    """Sem indicar quem é quem, o operador procura antes de julgar — e o
    orçamento de trinta segundos por item já se foi."""
    com_destaque = render_clip(cena(5), pedido(tmp_path, 0.0, 0.2, pessoas=(1,)))
    sem_destaque = render_clip(
        cena(5),
        ClipRequest(0.0, 0.2, frozenset(), None, tmp_path / "neutro.gif"),
    )

    a = np.asarray(Image.open(com_destaque).convert("RGB"))
    b = np.asarray(Image.open(sem_destaque).convert("RGB"))

    assert not np.array_equal(a, b)


def test_cria_o_diretorio_de_saida(tmp_path):
    destino = tmp_path / "fundo" / "mais" / "clipe.gif"
    render_clip(cena(), ClipRequest(0.0, 0.4, frozenset({1}), 1001, destino))

    assert destino.exists()
