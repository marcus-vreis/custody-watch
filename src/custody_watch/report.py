"""Página de revisão para o operador.

O sistema entrega uma fila ranqueada, não um alarme. Até agora essa fila
existia apenas como uma lista de dataclasses: ninguém conseguia olhar para
ela, e a hipótese central do produto — que um humano revisa cada item em
torno de trinta segundos e decide — nunca tinha sido testável.

## Três decisões de conteúdo, não de estilo

**O cabeçalho declara o que o dado é.** Rodando no CAVIAR, todo alerta é
falso por construção, porque o dataset não contém furto. Uma página que
mostrasse "3 alertas" sem dizer isso induziria o leitor a achar que o sistema
encontrou alguma coisa.

**Nenhum item afirma furto.** O texto diz o que foi observado — bagagem
retirada por quem não é do grupo dono — e nunca nomeia crime. A diferença
importa: o operador decide, o sistema ordena.

**A evidência numérica fica visível, não escondida atrás de um clique.**
É o que permite discordar do sistema. Um score sem os números que o
produziram é uma caixa-preta pedindo obediência.
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass, field
from pathlib import Path

NIVEIS = {
    "N3": ("evento de custódia", "n3"),
    "N2": ("contato com bagagem alheia", "n2"),
    "N1": ("permanência prolongada", "n1"),
}


@dataclass(frozen=True)
class ReviewItem:
    rank: int
    person: int
    score: float
    level: str
    clip_start: float
    clip_end: float
    explanations: list[str]
    clip_path: Path | None = None


@dataclass(frozen=True)
class SessionReport:
    name: str
    duration_s: float
    frames: int
    events: dict[str, int] = field(default_factory=dict)
    items: list[ReviewItem] = field(default_factory=list)


def _timestamp(segundos: float) -> str:
    minutos, resto = divmod(int(segundos), 60)
    return f"{minutos:02d}:{resto:02d}"


def _data_uri(caminho: Path | None) -> str | None:
    """GIF embutido na página: o artifact bloqueia host externo."""
    if caminho is None or not caminho.exists():
        return None
    dados = base64.b64encode(caminho.read_bytes()).decode("ascii")
    return f"data:image/gif;base64,{dados}"


def _cartao(item: ReviewItem) -> str:
    rotulo, classe = NIVEIS.get(item.level, ("sinalizado", "n1"))
    uri = _data_uri(item.clip_path)

    if uri:
        midia = (
            f'<img class="clipe" src="{uri}" alt="Trecho de {_timestamp(item.clip_start)} '
            f'a {_timestamp(item.clip_end)}, com a pessoa sinalizada e a bagagem destacadas">'
        )
    else:
        midia = '<div class="clipe vazio">trecho indisponível</div>'

    explicacoes = "".join(f"<li>{html.escape(t)}</li>" for t in item.explanations)
    janela = f"{_timestamp(item.clip_start)}&thinsp;–&thinsp;{_timestamp(item.clip_end)}"

    return f"""<article class="item">
  <div class="ordem"><span class="rank">{item.rank}</span></div>
  <div class="midia">{midia}</div>
  <div class="corpo">
    <header class="topo">
      <span class="chip {classe}">{html.escape(rotulo)}</span>
      <span class="janela">{janela}</span>
    </header>
    <ul class="porques">{explicacoes}</ul>
    <dl class="evidencia">
      <div><dt>pessoa</dt><dd>#{item.person}</dd></div>
      <div><dt>score</dt><dd>{item.score:.1f}</dd></div>
      <div><dt>sinais</dt><dd>{len(item.explanations)}</dd></div>
    </dl>
  </div>
</article>"""


def _sessao(sessao: SessionReport) -> str:
    if sessao.items:
        itens = "".join(_cartao(i) for i in sessao.items)
    else:
        itens = '<p class="silencio">Nenhum item chegou à fila. O sistema ficou calado.</p>'

    eventos = "".join(
        f"<div><dt>{html.escape(k.replace('_', ' '))}</dt><dd>{v}</dd></div>"
        for k, v in sorted(sessao.events.items())
        if v
    )

    return f"""<section class="sessao">
  <div class="sessao-cab">
    <h2>{html.escape(sessao.name)}</h2>
    <p class="meta">{sessao.frames} quadros · {_timestamp(sessao.duration_s)} de vídeo</p>
  </div>
  <dl class="eventos">{eventos}</dl>
  <div class="fila">{itens}</div>
</section>"""


def render_report(
    sessions: list[SessionReport],
    *,
    titulo: str = "Revisão de Custódia",
    aviso: str = "",
) -> str:
    total = sum(len(s.items) for s in sessions)
    minutos = sum(s.duration_s for s in sessions) / 60.0
    corpo = "".join(_sessao(s) for s in sessions)

    bloco_aviso = f'<p class="aviso">{html.escape(aviso)}</p>' if aviso else ""

    return f"""<title>{html.escape(titulo)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {{
  --ground: #F1F3F5;
  --surface: #FFFFFF;
  --raised: #E8ECEF;
  --ink: #14181C;
  --muted: #667484;
  --hairline: #D3DAE0;
  --accent: #C4551F;
  --grave: #B03A2E;
  --media: #B5762A;
  --leve: #5E7387;
  --chip-ink: #FFFFFF;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground: #0E1216;
    --surface: #161C22;
    --raised: #1E262E;
    --ink: #E2E8EE;
    --muted: #8A98A6;
    --hairline: #2A343D;
    --accent: #E0662E;
    --grave: #D9584B;
    --media: #D2913C;
    --leve: #7E95A8;
    --chip-ink: #10151A;
  }}
}}
:root[data-theme="dark"] {{
  --ground: #0E1216;
  --surface: #161C22;
  --raised: #1E262E;
  --ink: #E2E8EE;
  --muted: #8A98A6;
  --hairline: #2A343D;
  --accent: #E0662E;
  --grave: #D9584B;
  --media: #D2913C;
  --leve: #7E95A8;
  --chip-ink: #10151A;
}}

* {{ box-sizing: border-box; }}

body {{
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: "IBM Plex Sans", system-ui, -apple-system, sans-serif;
  font-size: 16px;
  line-height: 1.6;
}}

.pagina {{
  max-width: 1080px;
  margin: 0 auto;
  padding: clamp(1.5rem, 4vw, 3.5rem) clamp(1rem, 4vw, 2rem) 5rem;
  display: flex;
  flex-direction: column;
  gap: 2.5rem;
}}

h1, h2 {{
  font-family: "Barlow Condensed", "IBM Plex Sans", sans-serif;
  font-weight: 600;
  letter-spacing: 0.01em;
  text-wrap: balance;
  margin: 0;
}}
h1 {{ font-size: clamp(2.4rem, 6vw, 3.6rem); line-height: 1.05; }}
h2 {{ font-size: 1.6rem; line-height: 1.15; }}

.eyebrow {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.74rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--accent);
  margin: 0 0 0.6rem;
}}

.chamada {{
  max-width: 62ch;
  color: var(--muted);
  margin: 0.9rem 0 0;
}}

.aviso {{
  max-width: 62ch;
  margin: 1.4rem 0 0;
  padding: 0.95rem 1.15rem;
  border-left: 3px solid var(--accent);
  background: var(--raised);
  border-radius: 0 4px 4px 0;
  font-size: 0.95rem;
}}

.placar {{
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  border: 1px solid var(--hairline);
  border-radius: 6px;
  background: var(--surface);
  overflow: hidden;
}}
.placar div {{
  flex: 1 1 8rem;
  padding: 1rem 1.15rem;
  border-right: 1px solid var(--hairline);
}}
.placar div:last-child {{ border-right: none; }}
.placar dt {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.7rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}}
.placar dd {{
  margin: 0.3rem 0 0;
  font-family: "Barlow Condensed", sans-serif;
  font-size: 2rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}}

.sessao {{ display: flex; flex-direction: column; gap: 1rem; }}
.sessao-cab {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  padding-bottom: 0.6rem;
  border-bottom: 2px solid var(--hairline);
}}
.meta, .janela {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.8rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}}

.eventos {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 1.6rem;
  margin: 0;
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.78rem;
}}
.eventos div {{ display: flex; gap: 0.4rem; }}
.eventos dt {{ color: var(--muted); }}
.eventos dd {{ margin: 0; font-weight: 500; font-variant-numeric: tabular-nums; }}

.fila {{ display: flex; flex-direction: column; gap: 1rem; }}

.item {{
  display: grid;
  grid-template-columns: 2.75rem minmax(0, 22rem) minmax(0, 1fr);
  gap: 1.25rem;
  align-items: start;
  padding: 1.25rem;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: 6px;
}}

.ordem {{ display: flex; justify-content: center; }}
.rank {{
  font-family: "Barlow Condensed", sans-serif;
  font-size: 1.9rem;
  font-weight: 600;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
  line-height: 1;
}}

.clipe {{
  width: 100%;
  display: block;
  border-radius: 3px;
  border: 1px solid var(--hairline);
  background: var(--raised);
}}
.clipe.vazio {{
  aspect-ratio: 4 / 3;
  display: grid;
  place-items: center;
  color: var(--muted);
  font-size: 0.85rem;
}}

.corpo {{ display: flex; flex-direction: column; gap: 0.9rem; min-width: 0; }}
.topo {{ display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }}

/* Severidade lida em FORMA antes de cor: preenchido, contornado, texto. */
.chip {{
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.7rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 0.24rem 0.6rem;
  border-radius: 3px;
  white-space: nowrap;
}}
.chip.n3 {{ background: var(--grave); color: var(--chip-ink); font-weight: 500; }}
.chip.n2 {{ border: 1px solid var(--media); color: var(--media); }}
.chip.n1 {{ color: var(--leve); padding-left: 0; }}

.porques {{
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-width: 62ch;
}}
.porques li {{
  padding-left: 0.9rem;
  border-left: 2px solid var(--hairline);
  font-size: 0.95rem;
}}

.evidencia {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem 1.5rem;
  margin: 0;
  padding-top: 0.7rem;
  border-top: 1px solid var(--hairline);
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.76rem;
}}
.evidencia div {{ display: flex; gap: 0.4rem; }}
.evidencia dt {{ color: var(--muted); }}
.evidencia dd {{ margin: 0; font-variant-numeric: tabular-nums; }}

.silencio {{
  margin: 0;
  padding: 1.5rem;
  background: var(--surface);
  border: 1px dashed var(--hairline);
  border-radius: 6px;
  color: var(--muted);
  font-size: 0.95rem;
}}

.rodape {{
  border-top: 1px solid var(--hairline);
  padding-top: 1.25rem;
  color: var(--muted);
  font-size: 0.85rem;
  max-width: 62ch;
}}

@media (max-width: 720px) {{
  .item {{ grid-template-columns: 2.25rem minmax(0, 1fr); }}
  .midia {{ grid-column: 1 / -1; }}
  .corpo {{ grid-column: 1 / -1; }}
}}
</style>

<div class="pagina">
  <header>
    <p class="eyebrow">custody-watch · revisão de custódia</p>
    <h1>{html.escape(titulo)}</h1>
    <p class="chamada">A fila ordena o que merece o tempo de um operador. Ela não
    acusa ninguém: cada item descreve o que foi observado e mostra os números que
    produziram aquela posição, para que discordar seja possível.</p>
    {bloco_aviso}
  </header>

  <dl class="placar">
    <div><dt>itens na fila</dt><dd>{total}</dd></div>
    <div><dt>vídeo revisado</dt><dd>{minutos:.1f}<span style="font-size:1rem"> min</span></dd></div>
    <div><dt>sessões</dt><dd>{len(sessions)}</dd></div>
  </dl>

  {corpo}

  <footer class="rodape">
    Cada trecho é recortado em torno do sinal mais grave, com a pessoa sinalizada
    e a bagagem destacadas. O sistema não faz reconhecimento facial e não
    identifica ninguém — apenas acompanha se a bagagem saiu com quem a deixou.
  </footer>
</div>"""


def write_report(sessions: list[SessionReport], destino: Path, **kwargs) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(render_report(sessions, **kwargs), encoding="utf-8")
    return destino
