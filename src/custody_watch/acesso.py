"""Token de acesso opcional da tela ao vivo (#135).

O token vem só de variável de ambiente: argumento de linha de comando aparece
na lista de processos e no histórico do shell, e arquivo versionado acaba
publicado. Quem tem o token pede `Authorization: Bearer`; o navegador, que não
põe cabeçalho em `<img src=/video>`, troca o token uma vez por um cookie de
sessão aleatório, `HttpOnly` e `SameSite=Strict`. O token nunca vai para
query string (fica em histórico, log de proxy e `Referer`) nem para o cookie.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Mapping

TOKEN_ENV = "CUSTODY_WATCH_TOKEN"
TOKEN_MIN = 32
TOKEN_MAX = 256
DIVERSIDADE_MIN = 8
"""Caracteres distintos no mínimo: barra `aaaa...` e `abab...` com tamanho
de sobra, que nenhum gerador de segredo produz."""

AUTORIZACAO_MAX = len("Bearer ") + TOKEN_MAX
COOKIE_MAX = 4096
"""Tetos dos cabeçalhos lidos antes de comparar: o resto é recusado sem
olhar, e nenhum pedido faz o servidor varrer kilobytes por credencial."""

COOKIE_SESSAO = "cw_sessao"
SESSAO_S = 12 * 3600.0
"""Um turno de sala de monitoramento; depois disso a página pede o token de novo."""
SESSOES_MAX = 64
"""Teto de sessões vivas. Cada login de quem tem o token cria uma; sem teto,
um script em laço encheria a memória de um processo que roda por dias."""

MENSAGEM_TOKEN_FRACO = (
    f"{TOKEN_ENV} recusado: use de {TOKEN_MIN} a {TOKEN_MAX} caracteres ASCII "
    f"visíveis, sem espaço, com ao menos {DIVERSIDADE_MIN} distintos. Gere um com "
    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
)


def token_valido(token: str) -> bool:
    """Tamanho dentro dos limites, só ASCII visível (cabe num cabeçalho sem
    ambiguidade de codificação) e diversidade mínima."""
    if not TOKEN_MIN <= len(token) <= TOKEN_MAX:
        return False
    if not all("!" <= c <= "~" for c in token):
        return False
    return len(set(token)) >= DIVERSIDADE_MIN


def token_do_ambiente(ambiente: Mapping[str, str]) -> str | None:
    """O token configurado, ou `None` se a variável não existe. Variável
    definida e vazia é token fraco, não ausência: quem a definiu queria
    proteção e a recusa na subida avisa."""
    return ambiente.get(TOKEN_ENV)


def _bytes(texto: str) -> bytes:
    # Cabeçalhos chegam decodificados em latin-1; volta aos bytes originais.
    return texto.encode("latin-1", "replace")


def bearer_confere(valores: Iterable[str], esperado: bytes) -> bool:
    """Um único `Authorization: Bearer <token>` igual ao esperado, comparado
    em tempo constante. Repetido, longo demais ou outro esquema: recusa."""
    valores = list(valores)
    if len(valores) != 1 or len(valores[0]) > AUTORIZACAO_MAX:
        return False
    esquema, _, credencial = valores[0].partition(" ")
    if esquema.lower() != "bearer" or not credencial:
        return False
    return hmac.compare_digest(_bytes(credencial), esperado)


def cookie_de_sessao(valores: Iterable[str]) -> str | None:
    """O valor do cookie de sessão nos cabeçalhos `Cookie`, sem regex: o
    texto vem do cliente e é varrido uma vez, com teto de tamanho."""
    valores = list(valores)
    if sum(len(v) for v in valores) > COOKIE_MAX:
        return None
    for valor in valores:
        for par in valor.split(";"):
            nome, igual, conteudo = par.strip().partition("=")
            if igual and nome == COOKIE_SESSAO:
                return conteudo
    return None


def cabecalho_cookie(valor: str, *, cifrado: bool) -> str:
    """`Set-Cookie` da sessão. `Secure` só com TLS: em HTTP o navegador
    descartaria o cookie e a tela em loopback nunca entraria."""
    atributos = f"{COOKIE_SESSAO}={valor}; Path=/; Max-Age={int(SESSAO_S)}; HttpOnly"
    atributos += "; SameSite=Strict"
    return atributos + ("; Secure" if cifrado else "")


class Sessoes:
    """Ids de sessão aleatórios, em memória, com validade e teto.

    Reiniciar o processo desfaz todas: o operador digita o token de novo.
    """

    def __init__(
        self,
        duracao_s: float = SESSAO_S,
        maximo: int = SESSOES_MAX,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._duracao_s = duracao_s
        self._maximo = maximo
        self._relogio = relogio
        self._vencimentos: dict[str, float] = {}  # ordem de criação
        self._trava = threading.Lock()

    def cria(self) -> str:
        valor = secrets.token_urlsafe(32)
        with self._trava:
            self._expira()
            self._vencimentos[valor] = self._relogio() + self._duracao_s
            while len(self._vencimentos) > self._maximo:
                del self._vencimentos[next(iter(self._vencimentos))]
        return valor

    def valida(self, apresentado: str) -> bool:
        """Compara contra cada sessão viva em tempo constante, sem parar na
        primeira que casa: nem o tempo nem o acerto de dicionário dizem qual
        prefixo existe."""
        alvo = _bytes(apresentado)
        with self._trava:
            self._expira()
            casou = False
            for valor in self._vencimentos:
                casou |= hmac.compare_digest(alvo, valor.encode("ascii"))
        return casou and bool(apresentado)

    def _expira(self) -> None:
        agora = self._relogio()
        for valor in [v for v, fim in self._vencimentos.items() if fim <= agora]:
            del self._vencimentos[valor]


__all__ = [
    "AUTORIZACAO_MAX",
    "COOKIE_MAX",
    "COOKIE_SESSAO",
    "MENSAGEM_TOKEN_FRACO",
    "SESSAO_S",
    "SESSOES_MAX",
    "TOKEN_ENV",
    "TOKEN_MAX",
    "TOKEN_MIN",
    "Sessoes",
    "bearer_confere",
    "cabecalho_cookie",
    "cookie_de_sessao",
    "token_do_ambiente",
    "token_valido",
]
