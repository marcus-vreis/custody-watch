"""A tela ao vivo servida por HTTP, só com a biblioteca padrão.

Três rotas bastam: a página, o vídeo anotado em MJPEG e o estado em JSON, que
a página busca duas vezes por segundo. MJPEG porque o navegador mostra sem
biblioteca nenhuma e o servidor não precisa de codec além do JPEG que o
OpenCV já tem; WebRTC ou HLS trariam dependência e latência de buffer para
resolver um problema -- muitos espectadores remotos -- que uma sala de
monitoramento não tem.

Escuta só na própria máquina por padrão. É imagem de câmera de aeroporto:
abrir para a rede é decisão explícita de quem roda, nunca um default -- e
exige TLS. Na rede, HTTP em claro deixa qualquer um no mesmo segmento
assistir a câmera, e um aviso no terminal não protege ninguém.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import socket
import ssl
import threading
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .acesso import (
    MENSAGEM_TOKEN_FRACO,
    TOKEN_ENV,
    Sessoes,
    bearer_confere,
    cabecalho_cookie,
    cookie_de_sessao,
    token_valido,
)
from .orchestrator import LiveSession
from .painel import Painel

HOST_PADRAO = "127.0.0.1"
PORTA_PADRAO = 8765
FRONTEIRA = "quadro"

QUADROS_POR_SEGUNDO_MAX = 15
"""Teto do vídeo que sai para cada aba. O processamento pode ir mais rápido;
a tela não ganha nada com isso e cada aba aberta pagaria em rede."""

HANDSHAKE_S = 10.0
"""Prazo do aperto de mão TLS. Sem ele, uma conexão que abre e não negocia
seguraria a thread dela para sempre."""

REENVIO_S = 1.0
"""Quadro repetido quando nada muda. Sem escrita, uma aba fechada nunca vira
erro de socket, e a thread dela ficaria esperando para sempre."""

CORPO_MAX = 4096
"""Teto do corpo de um POST. São dois números e um id; qualquer coisa maior é
engano ou abuso, e ler o que o cliente mandar é como se enche a memória de um
processo que roda por dias."""

LEITURA_S = 10.0
"""Prazo de cada leitura ou escrita no socket de uma conexão. Sem ele, um
cliente que anuncia `Content-Length` maior que o corpo -- ou que conecta e
não manda nada -- segura a thread dele para sempre."""

NOMES_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1"})
CURINGAS = frozenset({"", "0.0.0.0", "::"})
"""Endereços de escuta que não são nome de ninguém: o navegador nunca os manda
como Host, e quem abre a tela usa o IP da máquina."""

TIPO_JSON = "application/json"

ROTA_LIVRE = "/healthz"
"""Única rota aberta com token: diz só que o processo atende."""
ROTA_SESSAO = "/sessao"
"""Troca o token (Bearer) por cookie de sessão. Só existe com token."""
DESAFIO = 'Bearer realm="custody-watch"'


def nomes_aceitos(host: str, extras: Iterable[str]) -> frozenset[str]:
    """Os nomes que o Host de um pedido pode trazer, em minúsculas e sem
    colchetes: os desta máquina, o de escuta quando é de rede e os declarados
    por quem sobe a tela."""
    nomes = set(NOMES_LOCAIS)
    for nome in (host, *extras):
        nome = nome.strip("[]").lower()
        if nome not in CURINGAS:
            nomes.add(nome)
    return frozenset(nomes)


def nome_permitido(texto: str) -> str:
    """Tipo de argparse para `--allowed-host`: um nome de rede puro, sem porta,
    esquema, usuário ou caminho. Um nome com porta nunca casaria com o Host
    e a recusa ficaria sem explicação."""
    nome = texto.strip("[]")
    if not nome or any(c.isspace() or c in "/@\\" for c in nome):
        raise argparse.ArgumentTypeError(f"nome de rede inválido: {texto!r}")
    if ":" in nome and not _ip_literal(nome):
        raise argparse.ArgumentTypeError(f"esperado só o nome, sem porta nem esquema: {texto!r}")
    return texto


def _host_e_porta(autoridade: str, porta_padrao: int) -> tuple[str, int] | None:
    """`exemplo:8080` vira `("exemplo", 8080)`; porta ausente é a do esquema.
    Qualquer coisa além de host e porta -- usuário, caminho -- não é Host."""
    try:  # colchete sem par e porta não numérica são ValueError
        partes = urlsplit("//" + autoridade)
        porta = partes.port
    except ValueError:
        return None
    if "@" in autoridade or partes.netloc != autoridade or not partes.hostname:
        return None
    return partes.hostname, porta if porta is not None else porta_padrao


def _ip_literal(nome: str) -> bool:
    try:
        ipaddress.ip_address(nome)
    except ValueError:
        return False
    return True


def host_aceito(
    autoridade: str | None,
    nomes: frozenset[str],
    *,
    porta: int,
    porta_padrao: int,
    qualquer_ip: bool = False,
) -> bool:
    """O Host aponta para esta tela? Barra DNS rebinding: o domínio do atacante
    pode resolver para cá, mas o navegador manda o nome dele no Host.

    `qualquer_ip` vale para escuta em curinga: IP literal não passa por DNS,
    então não serve a rebinding, e o IP da máquina não se sabe de antemão.
    """
    endereco = _host_e_porta(autoridade or "", porta_padrao)
    if endereco is None or endereco[1] != porta:
        return False
    nome = endereco[0]
    return nome in nomes or (qualquer_ip and _ip_literal(nome))


def _recusa_constante(nome: str) -> float:
    raise ValueError(f"{nome} não é um número aceito")


def _float_finito(texto: str) -> float:
    valor = float(texto)
    if not math.isfinite(valor):
        raise ValueError(f"número fora do intervalo: {texto[:40]}")
    return valor


class _Atendente(BaseHTTPRequestHandler):
    server: Servidor

    def setup(self) -> None:
        self.timeout = self.server.prazo_leitura_s
        self.raw_requestline = b""
        super().setup()

    def log_error(self, format: str, *args: object) -> None:
        """Calado só no prazo de uma conexão que nunca mandou pedido: o
        navegador abre conexões especulativas e não as usa, e cada uma viraria
        uma linha no terminal. Qualquer outro erro continua saindo."""
        estourou_prazo = bool(args) and isinstance(args[0], TimeoutError)
        if estourou_prazo and not self.raw_requestline:
            return
        super().log_error(format, *args)

    def parse_request(self) -> bool:
        """Ponto único, antes de qualquer rota e de qualquer método: Host e
        Origin desta tela, ou recusa antes de rotear ou usar o corpo."""
        if not super().parse_request():
            return False
        hosts = self.headers.get_all("Host") or []
        autoridade = hosts[0] if len(hosts) == 1 else None
        if not self.server.aceita_host(autoridade):
            self._recusa_sem_ler(421, "Host não permitido: abra a tela pelo endereço do servidor")
            return False
        origens = self.headers.get_all("Origin") or []
        if len(origens) > 1 or (origens and not self.server.aceita_origem(origens[0], autoridade)):
            self._recusa_sem_ler(403, "origem não permitida: só a própria tela faz este pedido")
            return False
        if not self._autorizado():
            self._recusa_sem_autorizacao()
            return False
        return True

    def _rota(self) -> str | None:
        try:
            return urlsplit(self.path).path
        except ValueError:  # colchete sem par vira ValueError no urlsplit
            return None

    def _autorizado(self) -> bool:
        """Com token: Bearer em qualquer rota; cookie de sessão em todas menos
        a que cria sessão, para um cookie não se renovar sozinho para sempre."""
        servidor = self.server
        if not servidor.exige_token:
            return True
        rota = self._rota()
        if rota == ROTA_LIVRE:
            return True
        if servidor.confere_bearer(self.headers.get_all("Authorization") or []):
            return True
        return rota != ROTA_SESSAO and servidor.confere_cookie(self.headers.get_all("Cookie") or [])

    def _recusa_sem_autorizacao(self) -> None:
        """401 sem ecoar nada do pedido. A página pedida sem credencial vira o
        formulário que troca o token por cookie: o navegador mostra o corpo de
        um 401 Bearer, e não há outra forma de pôr cabeçalho em `<img>`."""
        self._descarta_corpo()
        if self.command == "GET" and self._rota() == "/":
            tipo, corpo = "text/html; charset=utf-8", ENTRADA.encode("utf-8")
        else:
            tipo = "application/json; charset=utf-8"
            corpo = json.dumps(
                {"erro": f"autenticação exigida: Authorization: Bearer com o {TOKEN_ENV}"},
                ensure_ascii=False,
            ).encode("utf-8")
        self._envia(401, tipo, corpo, extras=(("WWW-Authenticate", DESAFIO),))

    def _entra(self) -> None:
        """Pedido já passou pelo Bearer em `parse_request`: cria a sessão."""
        self._descarta_corpo()
        valor = self.server.sessoes.cria()
        self.send_response(204)
        self.send_header("Set-Cookie", cabecalho_cookie(valor, cifrado=self.server.cifrado))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _recusa_sem_ler(self, codigo: int, motivo: str) -> None:
        """Recusa e fecha a conexão antes de usar o corpo. O corpo pequeno é
        lido e descartado: no Windows, fechar com bytes não lidos no socket
        vira RST, e o cliente perde a resposta de recusa."""
        self._descarta_corpo()
        self._recusa(codigo, motivo)

    def _descarta_corpo(self) -> None:
        """Fecha a conexão depois desta resposta, lendo antes o corpo pequeno
        que o pedido anunciou."""
        self.close_connection = True
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
            if 0 < tamanho <= CORPO_MAX:
                self.rfile.read(tamanho)
        except (ValueError, OSError):  # OSError inclui o prazo de leitura
            pass

    def _metodo_sem_rota(self) -> None:
        """Método que esta tela não atende. Sem isto, a classe base responde
        501 e fecha sem ler o corpo, e o cliente no Windows perde a resposta."""
        # A classe base registrava esta recusa no terminal; continua saindo.
        self.log_error("code %d, message %s", 501, f"Unsupported method ({self.command!r})")
        self._recusa_sem_ler(501, "método não suportado")

    do_PUT = do_PATCH = do_DELETE = _metodo_sem_rota

    def do_GET(self) -> None:
        rota = urlsplit(self.path).path
        painel = self.server.painel
        if rota == "/":
            self._envia(200, "text/html; charset=utf-8", PAGINA.encode("utf-8"))
        elif rota == "/healthz":
            # Só diz que o processo atende: sem estado da fonte, vídeo ou pessoas.
            self._envia(200, "application/json; charset=utf-8", b'{"status":"ok"}')
        elif rota == "/estado":
            corpo = json.dumps(painel.estado(), ensure_ascii=False).encode("utf-8")
            self._envia(200, "application/json; charset=utf-8", corpo)
        elif rota == "/quadro.jpg":
            segment = parse_qs(urlsplit(self.path).query).get("segment_id", [None])[0]
            jpeg = painel.jpeg(segment)
            if jpeg is None:
                self._envia(503, "text/plain; charset=utf-8", b"nenhum quadro ainda")
            else:
                self._envia(200, "image/jpeg", jpeg)
        elif rota == "/video":
            self._video()
        else:
            self._envia(404, "text/plain; charset=utf-8", "não existe".encode())

    def do_POST(self) -> None:
        """Apontar: o operador diz o que o detector não vê.

        As duas rotas recebem o clique em **fração do quadro**, porque o
        navegador mostra a imagem reduzida e pixel de tela apontaria outro
        lugar da cena.
        """
        rota = urlsplit(self.path).path
        if rota == ROTA_SESSAO and self.server.exige_token:
            self._entra()
            return
        if rota not in ("/ancora", "/dono"):
            self._descarta_corpo()
            self._envia(404, "text/plain; charset=utf-8", "não existe".encode())
            return
        # JSON obriga o navegador a perguntar antes (preflight) em pedido de
        # outra origem; `text/plain` e formulário passariam direto.
        tipo = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if tipo != TIPO_JSON:
            self._recusa_sem_ler(415, f"corpo precisa ser {TIPO_JSON}")
            return

        try:
            pedido = self._corpo()
        except TimeoutError:
            self.close_connection = True
            self._recusa(408, "corpo incompleto: prazo de leitura esgotado")
            return
        except (ValueError, TypeError) as erro:
            self._recusa(400, str(erro) or "pedido inválido")
            return
        # Ler corpo de rede fora da trava: cliente lento não atrasa a troca.
        with self.server.session_lock:
            self._aponta(rota, pedido)

    def _aponta(self, rota: str, pedido: dict) -> None:
        sessao = self.server.sessao
        if sessao is None:
            self._recusa(409, "esta tela não tem sessão ao vivo para apontar")
            return
        atual = self.server.painel.estado()
        segment = self.server.segment_id
        if segment is not None and (
            pedido.get("segment_id") != segment or atual.get("segment_id") != segment
        ):
            self._recusa(409, "segmento mudou: aguarde a imagem da sessão atual")
            return
        if atual.get("fonte", {}).get("historical", False):
            self._recusa(409, "imagem histórica: aguarde uma captura recente para apontar")
            return
        tamanho = self.server.painel.tamanho()
        if tamanho is None:
            self._recusa(503, "nenhum quadro ainda")
            return

        try:
            px, py = _ponto(pedido, tamanho)
            if rota == "/ancora":
                bagagem = sessao.aponta_bagagem(px, py)
            else:
                bagagem = sessao.aponta_dono(int(pedido["bagagem"]), px, py)
        except (ValueError, KeyError, TypeError, OverflowError) as erro:
            # OverflowError: inteiro do JSON grande demais para virar float.
            self._recusa(400, str(erro) or "pedido inválido")
            return

        corpo = json.dumps({"bagagem": bagagem.bag_id}, ensure_ascii=False).encode("utf-8")
        self._envia(200, "application/json; charset=utf-8", corpo)

    def _corpo(self) -> dict:
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho <= 0 or tamanho > CORPO_MAX:
            raise ValueError(f"corpo de {tamanho} bytes; o teto é {CORPO_MAX}")
        dados = self.rfile.read(tamanho)
        try:
            # Infinity, NaN e 1e999 não são coordenada nem id de bagagem.
            pedido = json.loads(dados, parse_constant=_recusa_constante, parse_float=_float_finito)
        except RecursionError:
            # "[" * 4000 cabe no teto de bytes e estoura a pilha do parser.
            raise ValueError("JSON aninhado demais") from None
        if not isinstance(pedido, dict):
            raise ValueError("esperado um objeto JSON")
        return pedido

    def _recusa(self, codigo: int, motivo: str) -> None:
        corpo = json.dumps({"erro": motivo}, ensure_ascii=False).encode("utf-8")
        self._envia(codigo, "application/json; charset=utf-8", corpo)

    def _envia(
        self, codigo: int, tipo: str, corpo: bytes, extras: Iterable[tuple[str, str]] = ()
    ) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for nome, valor in extras:
            self.send_header(nome, valor)
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
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            return  # a aba fechou, ou parou de ler além do prazo

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Silencioso no caminho feliz: a página pede o estado duas vezes por
        segundo, e o terminal viraria só isso. Erro continua saindo."""


def contexto_tls(certificado: Path | str, chave: Path | str) -> ssl.SSLContext:
    """Contexto de servidor com o certificado de quem instala; TLS 1.2 no mínimo."""
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.load_cert_chain(certificado, chave)
    return contexto


def so_nesta_maquina(host: str) -> bool:
    """O endereço nunca sai da máquina? Nome que não seja `localhost` conta
    como rede: resolver aqui para decidir seria confiar no DNS."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ponto(pedido: dict, tamanho: tuple[float, float]) -> tuple[float, float]:
    """A fração clicada vira pixel do quadro. Fora de [0,1] é engano: o clique
    saiu da imagem, e projetar isso apontaria um lugar que a câmera não vê."""
    largura, altura = tamanho
    x, y = float(pedido["x"]), float(pedido["y"])
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError(f"clique fora do quadro: ({x}, {y})")
    return x * largura, y * altura


class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    prazo_leitura_s: float = LEITURA_S

    def __init__(
        self,
        painel: Painel,
        host: str = HOST_PADRAO,
        porta: int = PORTA_PADRAO,
        tls: ssl.SSLContext | None = None,
        sessao: LiveSession | None = None,
        nomes_permitidos: Iterable[str] = (),
        token: str | None = None,
    ) -> None:
        """`nomes_permitidos`: nomes de rede extras que o Host pode trazer,
        além dos desta máquina e do endereço de escuta. `token`: com ele, toda
        rota menos `/healthz` exige o token ou uma sessão trocada por ele."""
        if tls is None and not so_nesta_maquina(host):
            raise ValueError(
                f"recusado escutar em {host} sem TLS: na rede, HTTP em claro deixa "
                f"qualquer um no mesmo segmento assistir a câmera. Passe --cert e --key"
            )
        if token is not None and not token_valido(token):
            raise ValueError(MENSAGEM_TOKEN_FRACO)
        if token is None and not so_nesta_maquina(host):
            raise ValueError(
                f"recusado escutar em {host} sem {TOKEN_ENV}: TLS cifra, mas não "
                f"identifica quem conecta, e qualquer um que alcance a porta veria a "
                f"câmera e apontaria na custódia. Defina a variável de ambiente {TOKEN_ENV}"
            )
        self._token = token.encode("ascii") if token is not None else None
        self.sessoes = Sessoes()
        self.painel = painel
        self.sessao = sessao
        self.segment_id = None
        self.session_lock = threading.Lock()
        """A sessão ao vivo, quando existe. Sem ela a tela é só leitura: uma
        gravação já encerrada não pode ganhar âncora nenhuma."""
        self.encerrando = threading.Event()
        self._no_ar = threading.Event()
        self._tls = tls
        self._nomes = nomes_aceitos(host, nomes_permitidos)
        self._curinga = host.strip("[]") in CURINGAS
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, porta), _Atendente)
        if tls is not None:
            # Sem aperto de mão no `accept`: ele travaria o laço que aceita
            # todas as outras conexões. `finish_request` o faz, com prazo.
            self.socket = tls.wrap_socket(
                self.socket, server_side=True, do_handshake_on_connect=False
            )

    def set_session(self, sessao: LiveSession | None, segment_id: str | None = None) -> None:
        """Troca atômica com os POSTs; referência velha nunca escreve na sessão nova."""
        with self.session_lock:
            self.sessao, self.segment_id = sessao, segment_id

    @property
    def cifrado(self) -> bool:
        return self._tls is not None

    @property
    def exige_token(self) -> bool:
        return self._token is not None

    def confere_bearer(self, valores: Iterable[str]) -> bool:
        return self._token is not None and bearer_confere(valores, self._token)

    def confere_cookie(self, valores: Iterable[str]) -> bool:
        valor = cookie_de_sessao(valores)
        return valor is not None and self.sessoes.valida(valor)

    @property
    def _esquema(self) -> str:
        return "https" if self.cifrado else "http"

    @property
    def _porta_padrao(self) -> int:
        return 443 if self.cifrado else 80

    def aceita_host(self, autoridade: str | None) -> bool:
        return host_aceito(
            autoridade,
            self._nomes,
            porta=self.porta,
            porta_padrao=self._porta_padrao,
            qualquer_ip=self._curinga,
        )

    def aceita_origem(self, origem: str, autoridade: str | None) -> bool:
        """Origin presente tem de ser esta tela: mesmo esquema, host e porta
        do Host já aceito. Barra o POST que outra página aberta no navegador
        do operador faria para cá. Sem Origin é cliente fora do navegador."""
        try:
            partes = urlsplit(origem)
        except ValueError:
            return False
        if partes.scheme != self._esquema or origem != f"{partes.scheme}://{partes.netloc}":
            return False
        aqui = _host_e_porta(autoridade or "", self._porta_padrao)
        return aqui is not None and _host_e_porta(partes.netloc, self._porta_padrao) == aqui

    def finish_request(self, request: socket.socket, client_address: tuple) -> None:
        """Com TLS, o aperto de mão acontece aqui, na thread da conexão.

        Fazê-lo no `accept` deixaria um único cliente que abre a conexão e
        não negocia tirar a tela do ar para todo mundo.

        Aperto de mão que falha termina calado. Uma aba esquecida em
        `http://` consulta o estado duas vezes por segundo, e cada tentativa
        virava um traceback inteiro no terminal; quem conectou errado já vê
        o erro do lado dele.
        """
        if isinstance(request, ssl.SSLSocket):
            request.settimeout(HANDSHAKE_S)
            try:
                request.do_handshake()
            except OSError:  # ssl.SSLError e o prazo do aperto de mão são OSError
                return
            request.settimeout(None)
        super().finish_request(request, client_address)

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
  color-scheme: light dark;
  /* Tokens da central multicâmera e da página de revisão: mesmos nomes e valores. */
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
  --muted-text: #556170;
  --accent-text: #A8430E;
  --edge: #7A8794;
  --selecao-fundo: #FBE6DA;
  --historico-fundo: #FFF1D6;
  --historico-borda: #B5762A;
  --historico-texto: #6B430A;
  --erro-texto: #8C2A20;
  --quadro: #080A0C;
  --display: Bahnschrift, "Trebuchet MS", sans-serif;
  --body: "Segoe UI", sans-serif; --mono: Consolas, "Liberation Mono", monospace;
  /* Próprios do painel: selo de severidade (texto sobre fundo cheio). */
  --grave-solid: #B03A2E;
  --media-solid: #87560E;
  --leve-solid: #556170;
  --on-solid: #FFFFFF;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
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
    --muted-text: #8A98A6;
    --accent-text: #F0814F;
    --edge: #687888;
    --selecao-fundo: #3A2A20;
    --historico-fundo: #291E10;
    --historico-borda: #9F783F;
    --historico-texto: #FFD394;
    --erro-texto: #FFB4A9;
    --quadro: #080A0C;
    --grave-solid: #D9584B;
    --media-solid: #D2913C;
    --leve-solid: #7E95A8;
    --on-solid: #14181C;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
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
  --muted-text: #8A98A6;
  --accent-text: #F0814F;
  --edge: #687888;
  --selecao-fundo: #3A2A20;
  --historico-fundo: #291E10;
  --historico-borda: #9F783F;
  --historico-texto: #FFD394;
  --erro-texto: #FFB4A9;
  --quadro: #080A0C;
  --grave-solid: #D9584B;
  --media-solid: #D2913C;
  --leve-solid: #7E95A8;
  --on-solid: #14181C;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--ground); color: var(--ink); font: .875rem/1.5 var(--body); }
button { font: inherit; cursor: pointer; touch-action: manipulation; }
button:disabled { cursor: default; opacity: .45; }
button:focus-visible, a:focus-visible, [tabindex]:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 4px;
}
header {
  display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: baseline;
  padding: 12px 16px; border-bottom: 1px solid var(--hairline); background: var(--surface);
}
header h1 { font: 500 1.0625rem/1.3 var(--display); letter-spacing: -.2px; margin: 0; }
.medida { color: var(--muted-text); font-variant-numeric: tabular-nums; }
.medida b { color: var(--ink); font-weight: 600; }
#conexao { margin-left: auto; font-size: .75rem; color: var(--muted-text); }
.barra { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.barra button {
  min-height: 36px; padding: 7px 13px; border-radius: 5px; font-size: .75rem; font-weight: 600;
  border: 1px solid var(--edge); background: var(--raised); color: var(--ink);
}
.barra button:hover:enabled { background: var(--hairline); border-color: var(--muted-text); }
.barra button[aria-pressed="true"] {
  background: var(--selecao-fundo); border-color: var(--accent); color: var(--accent-text);
}
#instrucao { color: var(--muted-text); font-size: .8125rem; }
#instrucao.erro { color: var(--erro-texto); }
#video.apontando { cursor: crosshair; }
#conexao.caiu { color: var(--erro-texto); font-weight: 600; }
main {
  display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 16px; padding: 16px;
}
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
#video {
  width: 100%; height: auto; display: block; background: var(--quadro); border-radius: 6px;
  min-height: 200px;
}
#video.historico { outline: 2px solid var(--historico-borda); }
#fonte { display: flex; gap: 6px 16px; flex-wrap: wrap; }
#fonte .detalhe { color: var(--historico-texto); }
section h2 {
  font: .75rem var(--mono); text-transform: uppercase; letter-spacing: 1.5px;
  color: var(--muted-text); margin: 0 0 8px; font-weight: 600;
}
section + section { margin-top: 20px; }
#avisos:empty { display: none; }
#journal:empty { display: none; }
.aviso {
  background: var(--historico-fundo); border: 1px solid var(--historico-borda);
  color: var(--historico-texto);
  border-radius: 6px; padding: 10px 12px; margin: 0 16px; margin-top: 12px;
}
.item {
  background: var(--surface); border: 1px solid var(--hairline); border-left-width: 4px;
  border-radius: 6px; padding: 10px 12px; margin-bottom: 8px;
}
.item.N3 { border-left-color: var(--grave); }
.item.N2 { border-left-color: var(--media); }
.item.N1, .item.N0 { border-left-color: var(--leve); }
.item .topo { display: flex; gap: 8px; align-items: baseline; }
.nivel {
  font-weight: 700; font-size: .75rem; padding: 1px 6px; border-radius: 4px;
  color: var(--on-solid);
}
.N3 .nivel { background: var(--grave-solid); }
.N2 .nivel { background: var(--media-solid); }
.N1 .nivel, .N0 .nivel { background: var(--leve-solid); }
.item ul { margin: 6px 0 0; padding-left: 18px; }
.vazio { color: var(--muted-text); font-style: italic; }
table { width: 100%; border-collapse: collapse; font-size: .8125rem; }
td { padding: 4px 6px; border-bottom: 1px solid var(--hairline); vertical-align: top; }
td.t { color: var(--muted-text); font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.estranho td { color: var(--erro-texto); font-weight: 600; }
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
<div id="fonte" class="aviso" role="status" aria-live="polite">Fonte inicializando…</div>
<div id="recovery" class="aviso" role="status" aria-live="polite" hidden></div>
<div id="journal" class="aviso" role="status" aria-live="polite"></div>
<div id="avisos"></div>
<main>
  <div>
    <div class="barra">
      <button id="btn-bagagem" type="button" aria-pressed="false">Apontar bagagem</button>
      <button id="btn-dono" type="button" aria-pressed="false" disabled>Apontar dono</button>
      <span id="instrucao"></span>
    </div>
    <img id="video" src="/video" alt="vídeo anotado ao vivo">
  </div>
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

function desenhaFonte(fonte) {
  const alvo = document.getElementById("fonte");
  const rotulos = {
    initializing: "Fonte inicializando",
    receiving: "Recebendo imagens",
    stale: "Sem imagens recentes",
    interrupted: "Captura interrompida",
    completed: "Arquivo concluído",
    stopped: "Encerramento solicitado",
  };
  const historica = !fonte || fonte.historical;
  const estado = fonte ? (rotulos[fonte.state] || "Estado da fonte indisponível")
                       : "Estado da fonte indisponível";
  const detalhes = [];
  if (fonte) {
    const segundos = (v) => v.toFixed(1) + " s";
    detalhes.push(fonte.received_age_s === null ? "Nenhum frame recebido."
      : "Última captura local há " + segundos(fonte.received_age_s) + ".");
    detalhes.push(fonte.image_age_s === null ? "Aguardando imagem processada."
      : "Imagem exibida capturada há " + segundos(fonte.image_age_s) + ".");
  }
  alvo.replaceChildren(el("b", "", (historica ? "Imagem histórica · " : "") + estado),
                       el("span", "detalhe", detalhes.join(" ")));
  document.getElementById("video").classList.toggle("historico", Boolean(historica));
  btnBagagem.disabled = Boolean(historica);
  btnDono.disabled = Boolean(historica) || ultimaBagagem === null;
  if (historica && modo) modoAponta(null);
}

function desenhaRecovery(recovery, segmento) {
  const alvo = document.getElementById("recovery");
  alvo.hidden = !recovery;
  if (!recovery) return;
  const estados = {
    initializing: "Reconexão inicializando", connecting: "Abrindo captura",
    loading_detector: "Iniciando detector novo", active: "Segmento ativo",
    backoff: "Aguardando nova tentativa", exhausted: "Tentativas de reconexão esgotadas",
    failed: "Reconexão interrompida", stopped: "Reconexão encerrada",
  };
  const motivos = {
    cleanup_unconfirmed: "liberação da captura anterior não confirmada",
    permanent_source_error: "erro permanente da fonte", journal_failed: "falha no histórico",
    archive_failed: "falha no arquivo de bagagens",
    processing_failed: "falha ao iniciar ou executar o detector",
    source_stale: "lacuna na recepção", read_failed: "leitura interrompida",
    capture_exception: "erro de captura", user_stop: "parada solicitada",
  };
  let texto = estados[recovery.state] || "Estado de reconexão indisponível";
  if (recovery.reason) texto += ": " + (motivos[recovery.reason] || "erro operacional");
  texto += " · tentativas nesta interrupção: " + recovery.retries;
  if (recovery.backoff_s !== null) texto += " · espera de " + recovery.backoff_s.toFixed(1) + " s";
  if (segmento) texto += ". Imagem do segmento " + segmento;
  texto += ". Uma lacuna inicia nova custódia; associações anteriores ficam no histórico.";
  alvo.textContent = texto;
}

async function quadroDoSegmento(segmento) {
  // /estado e MJPEG são conexões independentes. No modo reconectável, só
  // habilitar cliques depois de decodificar um JPEG do namespace pedido.
  const r = await fetch("/quadro.jpg?segment_id=" + encodeURIComponent(segmento),
                        { cache: "no-store" });
  if (!r.ok) throw new Error("aguardando quadro do segmento");
  const url = URL.createObjectURL(await r.blob());
  const imagem = new Image();
  try {
    imagem.src = url;
    await imagem.decode();
    video.src = url;
    await video.decode();
    if (quadroURL) URL.revokeObjectURL(quadroURL);
    quadroURL = url;
  } catch (erro) {
    URL.revokeObjectURL(url);
    throw erro;
  }
  if (segmento !== segmentoExibido) {
    ultimaBagagem = null;
    modo = null;
    modoAponta(null);
    segmentoExibido = segmento;
  }
}

function desenhaJournal(journal) {
  const alvo = document.getElementById("journal");
  alvo.replaceChildren();
  if (!journal) return;
  let texto;
  if (journal.state === "disabled") {
    texto = "Histórico apenas em memória; gravação em disco desativada.";
  }
  else if (journal.state === "failed" || journal.unconfirmed_events > 0) {
    texto = "Falha no histórico: " + journal.unconfirmed_events +
            " eventos sem confirmação de gravação. Eventos recentes continuam em memória.";
  } else {
    texto = "Histórico em disco: " + journal.written_events + " eventos gravados.";
    if (!journal.fsync) texto += " Sem confirmação de sincronização durável.";
    else if (journal.pending_records || journal.synced_through !== journal.last_written_sequence) {
      texto += " Sincronização pendente.";
    } else texto += " Última gravação sincronizada.";
  }
  if (journal.retained_events !== undefined) {
    texto += " Em memória: " + journal.retained_events +
             " de " + journal.total_events + " eventos.";
  }
  const bags = journal.bag_retention;
  if (bags?.enabled) {
    texto += " Bagagens em memória: " + bags.resident_bags +
             "; terminais com gravação confirmada: " + bags.archived_bags + ".";
    if (bags.state === "failed") {
      texto += " Falha no arquivo de bagagens; processamento bloqueado nesta sessão.";
    }
  }
  alvo.append(el("span", "", texto));
}

async function atualiza() {
  const conexao = document.getElementById("conexao");
  try {
    const r = await fetch("/estado", { cache: "no-store" });
    // Sessão venceu ou o servidor reiniciou: a página volta ao formulário do token.
    if (r.status === 401) { location.reload(); return; }
    if (!r.ok) throw new Error("servidor indisponível");
    const e = await r.json();
    desenhaRecovery(e.recovery, e.segment_id);
    if (e.segment_id) await quadroDoSegmento(e.segment_id);
    desenhaFonte(e.fonte);
    desenhaJournal(e.journal);
    if (e.t !== undefined) {
      document.getElementById("t").textContent = relogio(e.t);
      document.getElementById("quadros").textContent = e.quadros;
      document.getElementById("ancoras").textContent = e.ancoras;
      desenhaFila(e.fila);
      desenhaEventos(e.eventos);
      desenhaAvisos(e.avisos);
    }
    conexao.textContent = "servidor conectado";
    conexao.className = "";
  } catch (erro) {
    desenhaFonte(null);
    conexao.textContent = "sem conexão com o servidor";
    conexao.className = "caiu";
  }
  setTimeout(atualiza, 500);
}

// Apontar: o detector não vê tudo, e quem está olhando a tela vê.
const video = document.getElementById("video");
const instrucao = document.getElementById("instrucao");
const btnBagagem = document.getElementById("btn-bagagem");
const btnDono = document.getElementById("btn-dono");
let modo = null;
let ultimaBagagem = null;
let segmentoExibido = null;
let quadroURL = null;

function diz(texto, erro) {
  instrucao.textContent = texto;
  instrucao.className = erro ? "erro" : "";
}

function modoAponta(novo) {
  modo = modo === novo ? null : novo;
  btnBagagem.setAttribute("aria-pressed", String(modo === "bagagem"));
  btnDono.setAttribute("aria-pressed", String(modo === "dono"));
  video.classList.toggle("apontando", modo !== null);
  if (modo === "bagagem") diz("Clique no pé da bagagem, onde ela toca o chão.");
  else if (modo === "dono") diz("Clique na pessoa dona da bagagem " + ultimaBagagem + ".");
  else diz("");
}

async function aponta(rota, carga) {
  const r = await fetch(rota, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(carga),
  });
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corpo.erro || "recusado");
  return corpo;
}

video.addEventListener("click", async (evento) => {
  if (!modo) return;
  const area = video.getBoundingClientRect();
  const carga = {
    x: (evento.clientX - area.left) / area.width,
    y: (evento.clientY - area.top) / area.height,
    segment_id: segmentoExibido,
  };
  try {
    if (modo === "bagagem") {
      const corpo = await aponta("/ancora", carga);
      if (carga.segment_id !== segmentoExibido) return;
      ultimaBagagem = corpo.bagagem;
      btnDono.disabled = false;
      modo = null;
      modoAponta("dono");
    } else {
      carga.bagagem = ultimaBagagem;
      const corpo = await aponta("/dono", carga);
      if (carga.segment_id !== segmentoExibido) return;
      modoAponta(null);
      diz("Bagagem " + corpo.bagagem + " com dono.");
    }
  } catch (erro) {
    diz(erro.message, true);
  }
});

btnBagagem.addEventListener("click", () => modoAponta("bagagem"));
btnDono.addEventListener("click", () => modoAponta("dono"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && modo) modoAponta(null);
});

// O servidor caiu ou reiniciou: tenta de novo, sem recarregar a página.
video.addEventListener("error", () => {
  if (segmentoExibido === null) {
    setTimeout(() => { video.src = "/video?" + Date.now(); }, 1000);
  }
});
atualiza();
</script>
</body>
</html>
"""


ENTRADA = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Custódia ao vivo · entrada</title>
<style>
:root { color-scheme: light dark; --ground: #F1F3F5; --surface: #FFFFFF; --ink: #14181C;
  --edge: #7A8794; --erro-texto: #8C2A20; --accent: #C4551F; }
@media (prefers-color-scheme: dark) {
  :root { --ground: #0E1216; --surface: #161C22; --ink: #E2E8EE; --edge: #687888;
    --erro-texto: #FFB4A9; --accent: #E0662E; }
}
body { margin: 0; background: var(--ground); color: var(--ink);
  font: .875rem/1.5 "Segoe UI", sans-serif; }
form { max-width: 360px; margin: 15vh auto 0; padding: 20px 16px; background: var(--surface);
  border: 1px solid var(--edge); border-radius: 6px; display: grid; gap: 10px; }
h1 { font: 500 1.0625rem/1.3 Bahnschrift, "Trebuchet MS", sans-serif; margin: 0; }
input, button { font: inherit; min-height: 36px; padding: 6px 10px; border-radius: 5px;
  border: 1px solid var(--edge); background: var(--ground); color: var(--ink); }
button { cursor: pointer; font-weight: 600; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
#motivo { color: var(--erro-texto); min-height: 1.5em; margin: 0; }
</style>
</head>
<body>
<form id="entrada">
  <h1>Custódia ao vivo</h1>
  <label for="token">Token de acesso</label>
  <input id="token" type="password" autocomplete="current-password" required>
  <button type="submit">Entrar</button>
  <p id="motivo" role="alert"></p>
</form>
<script>
// O token vai só no cabeçalho Authorization deste POST; a resposta é um
// cookie HttpOnly que o navegador manda sozinho, inclusive no vídeo.
document.getElementById("entrada").addEventListener("submit", async (evento) => {
  evento.preventDefault();
  const campo = document.getElementById("token");
  const motivo = document.getElementById("motivo");
  let r = null;
  try {
    r = await fetch("/sessao", {
      method: "POST", headers: { "Authorization": "Bearer " + campo.value },
    });
  } catch (erro) { motivo.textContent = "Sem conexão com o servidor."; return; }
  campo.value = "";
  if (r.status === 204) location.reload();
  else motivo.textContent = "Token recusado.";
});
</script>
</body>
</html>
"""
"""Corpo do 401 da página: só o formulário, sem nada do painel."""


__all__ = ["HOST_PADRAO", "PORTA_PADRAO", "Servidor", "contexto_tls", "so_nesta_maquina"]
