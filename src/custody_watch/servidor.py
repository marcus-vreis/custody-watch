"""A tela ao vivo servida por HTTP, só com a biblioteca padrão.

Três rotas bastam: a página, o vídeo anotado em MJPEG e o estado em JSON, que
a página busca duas vezes por segundo. MJPEG porque o navegador mostra sem
biblioteca nenhuma e o servidor não precisa de codec além do JPEG que o
OpenCV já tem; WebRTC ou HLS trariam dependência e latência de buffer para
resolver um problema -- muitos espectadores remotos -- que uma sala de
monitoramento não tem.

Escuta só na própria máquina por padrão. É imagem de câmera de aeroporto:
abrir para a rede é decisão explícita de quem roda, nunca um default.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .painel import Painel

HOST_PADRAO = "127.0.0.1"
PORTA_PADRAO = 8765
FRONTEIRA = "quadro"

QUADROS_POR_SEGUNDO_MAX = 15
"""Teto do vídeo que sai para cada aba. O processamento pode ir mais rápido;
a tela não ganha nada com isso e cada aba aberta pagaria em rede."""

REENVIO_S = 1.0
"""Quadro repetido quando nada muda. Sem escrita, uma aba fechada nunca vira
erro de socket, e a thread dela ficaria esperando para sempre."""


class _Atendente(BaseHTTPRequestHandler):
    server: Servidor

    def do_GET(self) -> None:
        rota = urlsplit(self.path).path
        painel = self.server.painel
        if rota == "/":
            self._envia(200, "text/html; charset=utf-8", PAGINA.encode("utf-8"))
        elif rota == "/estado":
            corpo = json.dumps(painel.estado(), ensure_ascii=False).encode("utf-8")
            self._envia(200, "application/json; charset=utf-8", corpo)
        elif rota == "/quadro.jpg":
            jpeg = painel.jpeg()
            if jpeg is None:
                self._envia(503, "text/plain; charset=utf-8", b"nenhum quadro ainda")
            else:
                self._envia(200, "image/jpeg", jpeg)
        elif rota == "/video":
            self._video()
        else:
            self._envia(404, "text/plain; charset=utf-8", "não existe".encode())

    def _envia(self, codigo: int, tipo: str, corpo: bytes) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _video(self) -> None:
        """MJPEG: uma resposta que não termina, uma parte por quadro.

        A fronteira vai **depois** de cada quadro, e não só antes do próximo:
        alguns navegadores só desenham a parte quando a fronteira seguinte
        chega, e o último quadro de uma gravação que acabou ficaria invisível.
        """
        encerrando = self.server.encerrando
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={FRONTEIRA}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        ultimo: bytes | None = None
        ultimo_envio = 0.0
        relogio = 0.0
        try:
            self.wfile.write(f"--{FRONTEIRA}\r\n".encode())
            while not encerrando.is_set():
                jpeg = self.server.painel.jpeg()
                if jpeg is not None and (jpeg is not ultimo or relogio - ultimo_envio >= REENVIO_S):
                    self.wfile.write(
                        f"Content-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                        + jpeg
                        + f"\r\n--{FRONTEIRA}\r\n".encode()
                    )
                    self.wfile.flush()
                    ultimo, ultimo_envio = jpeg, relogio
                encerrando.wait(1.0 / QUADROS_POR_SEGUNDO_MAX)
                relogio += 1.0 / QUADROS_POR_SEGUNDO_MAX
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # a aba fechou

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Silencioso no caminho feliz: a página pede o estado duas vezes por
        segundo, e o terminal viraria só isso. Erro continua saindo."""


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, painel: Painel, host: str = HOST_PADRAO, porta: int = PORTA_PADRAO) -> None:
        self.painel = painel
        self.encerrando = threading.Event()
        self._no_ar = threading.Event()
        super().__init__((host, porta), _Atendente)

    @property
    def porta(self) -> int:
        return int(self.server_address[1])

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._no_ar.set()
        super().serve_forever(poll_interval)

    def encerra(self) -> None:
        """Fecha os vídeos abertos e para de aceitar conexões.

        `shutdown` sozinho não basta: ele para o laço de aceitar, mas cada
        vídeo aberto é uma resposta que não termina, e seguraria a thread
        dela viva depois do Ctrl+C.
        """
        self.encerrando.set()
        if self._no_ar.is_set():
            self.shutdown()
        self.server_close()


PAGINA = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Custódia ao vivo</title>
<style>
:root {
  --fundo: #f4f5f7; --painel: #ffffff; --texto: #1d2129; --suave: #5f6673;
  --borda: #dde0e6; --n3: #d42f2f; --n2: #d9822b; --ancora: #1f8fb3;
  --aviso-fundo: #fff4d6; --aviso-borda: #e0b43a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fundo: #111418; --painel: #1a1f26; --texto: #e6e9ee; --suave: #9aa3b0;
    --borda: #2c333d; --n3: #ff5c5c; --n2: #f4a261; --ancora: #48cae4;
    --aviso-fundo: #3a3016; --aviso-borda: #b8902a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--fundo); color: var(--texto);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
header {
  display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: baseline;
  padding: 12px 16px; border-bottom: 1px solid var(--borda); background: var(--painel);
}
header h1 { font-size: 16px; margin: 0; font-weight: 600; }
.medida { color: var(--suave); font-variant-numeric: tabular-nums; }
.medida b { color: var(--texto); font-weight: 600; }
#conexao { margin-left: auto; font-size: 12px; color: var(--suave); }
#conexao.caiu { color: var(--n3); font-weight: 600; }
main {
  display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; padding: 16px;
}
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
#video {
  width: 100%; height: auto; display: block; background: #000; border-radius: 6px;
  min-height: 200px;
}
section h2 {
  font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--suave); margin: 0 0 8px; font-weight: 600;
}
section + section { margin-top: 20px; }
#avisos:empty { display: none; }
.aviso {
  background: var(--aviso-fundo); border: 1px solid var(--aviso-borda);
  border-radius: 6px; padding: 10px 12px; margin: 0 16px; margin-top: 12px;
}
.item {
  background: var(--painel); border: 1px solid var(--borda); border-left-width: 4px;
  border-radius: 6px; padding: 10px 12px; margin-bottom: 8px;
}
.item.N3 { border-left-color: var(--n3); }
.item.N2 { border-left-color: var(--n2); }
.item .topo { display: flex; gap: 8px; align-items: baseline; }
.nivel { font-weight: 700; font-size: 12px; padding: 1px 6px; border-radius: 4px; color: #fff; }
.N3 .nivel { background: var(--n3); }
.N2 .nivel { background: var(--n2); }
.N1 .nivel, .N0 .nivel { background: var(--suave); }
.item ul { margin: 6px 0 0; padding-left: 18px; }
.vazio { color: var(--suave); font-style: italic; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
td { padding: 4px 6px; border-bottom: 1px solid var(--borda); vertical-align: top; }
td.t { color: var(--suave); font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.estranho td { color: var(--n3); font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Custódia ao vivo</h1>
  <span class="medida">tempo <b id="t">–</b></span>
  <span class="medida">quadros <b id="quadros">–</b></span>
  <span class="medida">bagagens em custódia <b id="ancoras">–</b></span>
  <span id="conexao">conectando…</span>
</header>
<div id="avisos"></div>
<main>
  <div><img id="video" src="/video" alt="vídeo anotado ao vivo"></div>
  <div>
    <section>
      <h2>Fila do operador</h2>
      <div id="fila"><p class="vazio">Nada na fila.</p></div>
    </section>
    <section>
      <h2>Eventos recentes</h2>
      <table><tbody id="eventos"></tbody></table>
    </section>
  </div>
</main>
<script>
const ROTULOS = {
  bag_appeared: "bagagem em custódia",
  bag_owned: "posse atribuída",
  bag_unattended: "bagagem desacompanhada",
  bag_reattended: "dono voltou",
  bag_ambiguous: "posse ambígua",
  bag_occluded: "bagagem encoberta",
  bag_removed_by_owner: "retirada pelo dono",
  bag_removed_by_stranger: "retirada por estranho",
  party_formed: "grupo formado",
  party_joined_strong: "entrou no grupo",
  party_joined_weak: "aproximação",
  track_relinked: "trilha religada",
};

function el(tag, classe, texto) {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

// Mesmo formato das frases da fila ("em 00:34"), com o décimo de segundo.
function relogio(t) {
  const s = Math.floor(t), m = Math.floor(s / 60);
  const dois = (n) => String(n).padStart(2, "0");
  return dois(m) + ":" + dois(s % 60) + "." + Math.floor((t - s) * 10);
}

function desenhaFila(fila) {
  const alvo = document.getElementById("fila");
  alvo.replaceChildren();
  if (!fila || !fila.length) { alvo.append(el("p", "vazio", "Nada na fila.")); return; }
  for (const item of fila) {
    const card = el("div", "item " + item.nivel);
    const topo = el("div", "topo");
    const quem = "#" + item.posicao + " pessoa " + item.pessoa;
    topo.append(el("span", "nivel", item.nivel), el("b", "", quem));
    const lista = el("ul");
    for (const frase of item.explicacoes) lista.append(el("li", "", frase));
    card.append(topo, lista);
    alvo.append(card);
  }
}

function desenhaEventos(eventos) {
  const alvo = document.getElementById("eventos");
  alvo.replaceChildren();
  for (const e of eventos || []) {
    const linha = el("tr", e.tipo === "bag_removed_by_stranger" ? "estranho" : "");
    const partes = [];
    if (e.bagagem !== null) partes.push("bagagem " + e.bagagem);
    if (e.pessoa !== null) partes.push("pessoa " + e.pessoa);
    linha.append(
      el("td", "t", relogio(e.t)),
      el("td", "", ROTULOS[e.tipo] || e.tipo),
      el("td", "", partes.join(", ")),
    );
    alvo.append(linha);
  }
}

function desenhaAvisos(avisos) {
  const alvo = document.getElementById("avisos");
  alvo.replaceChildren();
  for (const a of avisos || []) alvo.append(el("div", "aviso", a));
}

async function atualiza() {
  const conexao = document.getElementById("conexao");
  try {
    const r = await fetch("/estado", { cache: "no-store" });
    const e = await r.json();
    if (e.t !== undefined) {
      document.getElementById("t").textContent = relogio(e.t);
      document.getElementById("quadros").textContent = e.quadros;
      document.getElementById("ancoras").textContent = e.ancoras;
      desenhaFila(e.fila);
      desenhaEventos(e.eventos);
      desenhaAvisos(e.avisos);
    }
    conexao.textContent = "ao vivo";
    conexao.className = "";
  } catch (erro) {
    conexao.textContent = "sem conexão com o servidor";
    conexao.className = "caiu";
  }
  setTimeout(atualiza, 500);
}

const video = document.getElementById("video");
// O servidor caiu ou reiniciou: tenta de novo, sem recarregar a página.
video.addEventListener("error", () => {
  setTimeout(() => { video.src = "/video?" + Date.now(); }, 1000);
});
atualiza();
</script>
</body>
</html>
"""


__all__ = ["HOST_PADRAO", "PORTA_PADRAO", "Servidor"]
