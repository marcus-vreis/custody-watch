from pathlib import Path

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
