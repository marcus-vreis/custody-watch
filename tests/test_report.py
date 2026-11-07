import base64
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from custody_watch.report import ReviewItem, SessionReport, render_report, write_report


def item(rank: int = 1, person: int = 7, score: float = 12.9, level: str = "N3") -> ReviewItem:
    return ReviewItem(
        rank=rank,
        person=person,
        score=score,
        level=level,
        clip_start=27.2,
        clip_end=47.2,
        explanations=["Bagagem 9 do grupo 1 foi retirada em 00:47 por pessoa fora do grupo."],
    )


def sessao(nome: str = "LeftBag", itens: list[ReviewItem] | None = None) -> SessionReport:
    return SessionReport(
        name=nome,
        duration_s=58.0,
        frames=1439,
        events={"bag_removed_by_stranger": 1, "bag_appeared": 1, "party_joined_weak": 0},
        items=[] if itens is None else itens,
    )


def test_pagina_tem_titulo_proprio():
    html = render_report([sessao()], titulo="Revisão de Custódia")

    assert "<title>Revisão de Custódia</title>" in html


def test_html_autocontido_declara_utf8_antes_do_texto_portugues():
    document = render_report([sessao()], titulo="Revisão de Custódia 🧳").encode("utf-8")
    assert b'<meta charset="utf-8">' in document[:1024]
    assert document.index(b'<meta charset="utf-8">') < document.index(b"<title>")


def test_conta_itens_e_duracao_no_placar():
    html = render_report([sessao(itens=[item(1), item(2)]), sessao("LeftBox")])

    assert "<dd>2</dd>" in html
    assert "1.9<span" in html  # 58s + 58s = 116s = 1.9 min


def test_explicacao_aparece_no_cartao():
    html = render_report([sessao(itens=[item()])])

    assert "foi retirada em 00:47 por pessoa fora do grupo" in html


def test_severidade_vira_classe_de_forma_nao_so_de_cor():
    """Chip preenchido, contornado ou texto: o nível lê sem depender de matiz."""
    grave = render_report([sessao(itens=[item(level="N3")])])
    leve = render_report([sessao(itens=[item(level="N1")])])

    assert 'class="chip n3"' in grave
    assert 'class="chip n1"' in leve


def test_nivel_desconhecido_nao_quebra_a_pagina():
    html = render_report([sessao(itens=[item(level="ZZ")])])

    assert 'class="chip n1"' in html
    assert "sinalizado" in html


def test_janela_do_clipe_vira_timestamp_legivel():
    html = render_report([sessao(itens=[item()])])

    assert "00:27" in html
    assert "00:47" in html


def test_evento_com_contagem_zero_nao_polui_o_resumo():
    html = render_report([sessao(itens=[item()])])

    assert "bag removed by stranger" in html
    assert "party joined weak" not in html


def test_sessao_sem_item_diz_que_o_sistema_ficou_calado():
    """Fila vazia é resultado, não falha — e precisa ser lida como tal."""
    html = render_report([sessao()])

    assert "ficou calado" in html


def test_pagina_declara_que_nao_acusa():
    """O sistema ordena; quem decide é o operador, e a página diz isso."""
    html = " ".join(render_report([sessao(itens=[item()])]).split())

    assert "não acusa ninguém" in html


def test_cartao_descreve_o_observado_sem_nomear_crime():
    """Nomear crime transforma triagem em acusação, que é o que o design recusa."""
    html = render_report([sessao(itens=[item()])]).lower()

    for palavra in ("furto", "roubo", "ladrão", "criminoso", "suspeito de"):
        assert palavra not in html


def test_aviso_e_escapado():
    html = render_report([sessao()], aviso="<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_clipe_ausente_vira_placeholder_e_nao_imagem_quebrada():
    quebrado = ReviewItem(
        rank=1,
        person=7,
        score=1.0,
        level="N2",
        clip_start=0.0,
        clip_end=1.0,
        explanations=["algo"],
        clip_path=Path("nao_existe.gif"),
    )
    html = render_report([sessao(itens=[quebrado])])

    assert "trecho indisponível" in html
    assert "<img" not in html


def test_clipe_existente_vira_data_uri(tmp_path):
    """O artifact bloqueia host externo: o GIF precisa viajar na página."""
    gif = tmp_path / "c.gif"
    gif.write_bytes(b"GIF89a\x01\x00\x01\x00\x00\x00\x00;")
    com_clipe = ReviewItem(
        rank=1,
        person=7,
        score=1.0,
        level="N3",
        clip_start=0.0,
        clip_end=1.0,
        explanations=["algo"],
        clip_path=gif,
    )

    html = render_report([sessao(itens=[com_clipe])])

    assert "data:image/gif;base64," in html
    assert "alt=" in html


def test_paleta_define_cor_fora_de_media_query():
    """Cor definida só dentro de media query some no tema 'sistema'."""
    html = render_report([sessao()])
    antes_da_media = html.split("@media (prefers-color-scheme")[0]

    for token in ("--ground:", "--ink:", "--accent:", "--grave:"):
        assert token in antes_da_media


def test_body_pinta_o_proprio_fundo():
    """Body transparente empresta o fundo do host e mistura os temas."""
    html = render_report([sessao()])

    assert "background: var(--ground)" in html


def test_write_report_cria_o_arquivo(tmp_path):
    destino = write_report([sessao()], tmp_path / "sub" / "fila.html")

    assert destino.exists()
    assert "custody-watch" in destino.read_text(encoding="utf-8")


def _com_clipe(tmp_path, rank, byte=b"a"):
    path = tmp_path / f"clip-{rank}-{byte.hex()}.gif"
    path.write_bytes(b"GIF89a" + byte * 2048)
    return replace(item(rank, person=rank), clip_path=path)


def test_orcamento_retira_clipes_do_fim_do_ranking_e_mantem_fila_e_explicacoes(tmp_path):
    top = _com_clipe(tmp_path, 1)
    low = _com_clipe(tmp_path, 2, b"b")
    sessions = [sessao(itens=[low, top])]
    full = render_report(sessions)
    limit = len(full.encode("utf-8")) - 1000

    bounded = render_report(sessions, max_bytes=limit)

    assert len(bounded.encode("utf-8")) <= limit
    assert bounded.count("data:image/gif;base64,") == 1
    assert base64.b64encode(top.clip_path.read_bytes()).decode() in bounded
    assert base64.b64encode(low.clip_path.read_bytes()).decode() not in bounded
    assert bounded.count('<article class="item">') == 2
    assert bounded.count("foi retirada em 00:47") == 2
    assert "clipe omitido pelo limite da página" in bounded
    assert sessions[0].items == [low, top]


def test_orcamento_e_compartilhado_entre_sessoes_com_prioridade_estavel(tmp_path):
    first = _com_clipe(tmp_path, 1)
    other = _com_clipe(tmp_path, 1, b"b")
    sessions = [sessao("Primeira", [first]), sessao("Segunda", [other])]
    full = render_report(sessions)
    bounded = render_report(sessions, max_bytes=len(full.encode("utf-8")) - 1000)
    assert bounded.count("data:image/gif;base64,") == 1
    assert base64.b64encode(first.clip_path.read_bytes()).decode() in bounded
    assert "Primeira" in bounded and "Segunda" in bounded


def test_limite_exato_conta_utf8_e_cabe_sem_omitir_clipes(tmp_path):
    session = sessao("áé漢字", [_com_clipe(tmp_path, 1)])
    full = render_report([session], titulo="Revisão 🧳")
    exact = len(full.encode("utf-8"))
    assert exact > len(full)
    assert render_report([session], titulo="Revisão 🧳", max_bytes=exact) == full


def test_clipe_que_nao_cabe_nao_e_lido_nem_substituido_por_rank_menor(tmp_path, monkeypatch):
    from custody_watch import report

    top = _com_clipe(tmp_path, 1)
    with top.clip_path.open("wb") as handle:
        handle.truncate(1_000_000)
    low = _com_clipe(tmp_path, 2)

    def fail(*_args, **_kwargs):
        pytest.fail("não ler mídia fora do orçamento")

    monkeypatch.setattr(report, "_data_uri", fail)
    bounded = render_report([sessao(itens=[top, low])], max_bytes=20_000)
    assert "data:image/gif;base64," not in bounded
    assert bounded.count("clipe omitido pelo limite da página") == 2


def test_limite_padrao_conta_expansao_base64_sem_ler_clipe_grande(tmp_path, monkeypatch):
    from custody_watch import report

    top = _com_clipe(tmp_path, 1)
    with top.clip_path.open("wb") as handle:
        handle.truncate(12_000_000)  # base64 sozinho ocuparia 16.000.000 bytes

    def fail(*_args, **_kwargs):
        pytest.fail("clipe que excede 15 MB após base64 não deve ser lido")

    monkeypatch.setattr(report, "_data_uri", fail)
    bounded = render_report([sessao(itens=[top])])
    assert len(bounded.encode("utf-8")) <= 15_000_000
    assert "clipe omitido pelo limite da página" in bounded


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, None])
def test_orcamento_exige_numero_inteiro_positivo(limit):
    with pytest.raises(ValueError, match="max_bytes"):
        render_report([sessao()], max_bytes=limit)


def test_texto_excede_limite_recusa_antes_de_sobrescrever_relatorio(tmp_path):
    target = tmp_path / "review.html"
    target.write_text("relatório anterior", encoding="utf-8")
    with pytest.raises(ValueError, match="conteúdo textual"):
        write_report([sessao()], target, max_bytes=1000)
    assert target.read_text(encoding="utf-8") == "relatório anterior"


def test_arquivo_gravado_respeita_bytes_utf8_sem_traduzir_lf_no_windows(tmp_path):
    sessions = [sessao("áé漢字", [_com_clipe(tmp_path, 1)])]
    full = render_report(sessions)
    limit = len(full.encode("utf-8"))
    target = write_report(sessions, tmp_path / "output.html", max_bytes=limit)
    assert target.stat().st_size == limit
    assert target.read_bytes() == full.encode("utf-8")


def test_clipe_modificado_apos_orcamento_recusa_sem_substituir_relatorio(tmp_path, monkeypatch):
    from custody_watch import report

    clip = _com_clipe(tmp_path, 1)
    original = report._data_uri

    def changed(path, expected_bytes):
        with path.open("ab") as handle:
            handle.write(b"changed")
        return original(path, expected_bytes)

    monkeypatch.setattr(report, "_data_uri", changed)
    target = tmp_path / "output.html"
    target.write_bytes(b"previous report")
    with pytest.raises(ValueError, match="mudou de tamanho"):
        write_report([sessao(itens=[clip])], target)
    assert target.read_bytes() == b"previous report"


@pytest.mark.parametrize(
    "script, arguments", [("watch_video.py", ["missing.mp4"]), ("build_report.py", [])]
)
@pytest.mark.parametrize("limit", ["0", "-1"])
def test_comandos_validam_orcamento_antes_de_ler_video_ou_dataset(script, arguments, limit):
    path = Path(__file__).resolve().parents[1] / "scripts" / script
    result = subprocess.run(
        [sys.executable, str(path), *arguments, "--report-max-bytes", limit],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "positivo" in result.stderr.decode(errors="replace")
