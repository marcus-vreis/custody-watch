"""Token de acesso opcional da tela ao vivo (#135).

Sem token configurado, nada muda em loopback. Com token, toda rota exige
`Authorization: Bearer` ou o cookie de sessão trocado por ele, exceto
`/healthz`. O token nunca volta em resposta, terminal ou journal.
"""

import hmac
import http.client
import json
import secrets
import ssl
import threading

import numpy as np
import pytest

from custody_watch.acesso import (
    COOKIE_SESSAO,
    TOKEN_ENV,
    TOKEN_MAX,
    TOKEN_MIN,
    Sessoes,
    cabecalho_cookie,
    token_valido,
)
from custody_watch.painel import Painel
from custody_watch.servidor import Servidor
from tests.test_servidor import _sessao_viva

REPETICOES = 150
"""Mesma corrida de #129: a recusa com corpo não lido vira RST no Windows."""


def _token() -> str:
    return secrets.token_urlsafe(32)


@pytest.fixture
def com_token():
    token = _token()
    painel = Painel()
    painel.atualiza(np.zeros((2000, 2000, 3), np.uint8), {"t": 1.0})
    sessao = _sessao_viva()
    servidor = Servidor(painel, "127.0.0.1", 0, sessao=sessao, token=token)
    thread = threading.Thread(target=servidor.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield token, servidor, sessao
    finally:
        servidor.encerra()
        thread.join(5.0)


def _pede(
    porta: int,
    metodo: str,
    rota: str,
    cabecalhos: dict | None = None,
    corpo: bytes | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=5.0)
    try:
        conexao.request(metodo, rota, body=corpo, headers=cabecalhos or {})
        resposta = conexao.getresponse()
        return resposta.status, resposta.headers, resposta.read()
    finally:
        conexao.close()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _json(carga: dict) -> bytes:
    return json.dumps(carga).encode("utf-8")


ROTAS_LEITURA = ["/", "/estado", "/quadro.jpg", "/rota-que-nao-existe"]


# --- sem token: o loopback de hoje -----------------------------------------


def test_sem_token_loopback_le_e_escreve_sem_cabecalho():
    painel = Painel()
    painel.atualiza(np.zeros((2000, 2000, 3), np.uint8), {"t": 2.0})
    servidor = Servidor(painel, "127.0.0.1", 0, sessao=_sessao_viva())
    thread = threading.Thread(target=servidor.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        status, cabecalhos, corpo = _pede(servidor.porta, "GET", "/estado")
        assert (status, json.loads(corpo)) == (200, {"t": 2.0})
        assert cabecalhos["WWW-Authenticate"] is None
        escrita = _pede(
            servidor.porta,
            "POST",
            "/ancora",
            {"Content-Type": "application/json"},
            _json({"x": 0.525, "y": 0.5}),
        )
        assert escrita[0] == 200
        assert _pede(servidor.porta, "POST", "/sessao")[0] == 404
    finally:
        servidor.encerra()
        thread.join(5.0)


# --- com token: leitura ----------------------------------------------------


@pytest.mark.parametrize("rota", ROTAS_LEITURA)
def test_com_token_leitura_sem_cabecalho_e_401_bearer(com_token, rota):
    token, servidor, _ = com_token
    status, cabecalhos, corpo = _pede(servidor.porta, "GET", rota)
    assert status == 401
    assert cabecalhos["WWW-Authenticate"].startswith("Bearer")
    assert cabecalhos["Cache-Control"] == "no-store"
    assert token.encode() not in corpo


def test_com_token_leitura_com_bearer_atende(com_token):
    token, servidor, _ = com_token
    status, _, corpo = _pede(servidor.porta, "GET", "/estado", _bearer(token))
    assert (status, json.loads(corpo)) == (200, {"t": 1.0})
    status, cabecalhos, corpo = _pede(servidor.porta, "GET", "/quadro.jpg", _bearer(token))
    assert status == 200 and corpo.startswith(b"\xff\xd8")
    status, cabecalhos, corpo = _pede(servidor.porta, "GET", "/", _bearer(token))
    assert status == 200 and b"/estado" in corpo


def test_com_token_video_exige_autorizacao(com_token):
    token, servidor, _ = com_token
    assert _pede(servidor.porta, "GET", "/video")[0] == 401
    conexao = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5.0)
    try:
        conexao.request("GET", "/video", headers=_bearer(token))
        resposta = conexao.getresponse()
        assert resposta.status == 200
        assert resposta.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        assert resposta.readline() == b"--quadro\r\n"
    finally:
        conexao.close()


def test_pagina_sem_autorizacao_e_o_formulario_de_entrada(com_token):
    """O navegador mostra o corpo de um 401 Bearer: ali vai o formulário que
    troca o token por cookie, sem expor nada do painel."""
    token, servidor, _ = com_token
    status, cabecalhos, corpo = _pede(servidor.porta, "GET", "/")
    texto = corpo.decode("utf-8")
    assert status == 401
    assert cabecalhos["Content-Type"].startswith("text/html")
    assert 'type="password"' in texto
    assert "/sessao" in texto
    assert "/estado" not in texto and "/video" not in texto
    assert token not in texto


@pytest.mark.parametrize(
    "valor",
    [
        "Basic dXN1YXJpbzpzZW5oYQ==",
        "Bearer",
        "Bearer ",
        "Bearer  {token}",
        "Bearer {token}x",
        "Bearer {token} {token}",
        "Bearer {curto}",
        "{token}",
        "Token {token}",
    ],
)
def test_autorizacao_malformada_ou_errada_e_401(com_token, valor):
    token, servidor, _ = com_token
    cabecalho = {"Authorization": valor.format(token=token, curto=token[:-1])}
    status, cabecalhos, corpo = _pede(servidor.porta, "GET", "/estado", cabecalho)
    assert status == 401
    assert cabecalhos["WWW-Authenticate"].startswith("Bearer")
    assert token.encode() not in corpo


def test_token_de_outro_servidor_e_401(com_token):
    _, servidor, _ = com_token
    assert _pede(servidor.porta, "GET", "/estado", _bearer(_token()))[0] == 401


def test_esquema_bearer_nao_diferencia_caixa(com_token):
    token, servidor, _ = com_token
    cabecalho = {"Authorization": f"bearer {token}"}
    assert _pede(servidor.porta, "GET", "/estado", cabecalho)[0] == 200


def test_autorizacao_repetida_e_401(com_token):
    token, servidor, _ = com_token
    conexao = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5.0)
    try:
        conexao.putrequest("GET", "/estado")
        conexao.putheader("Authorization", f"Bearer {token}")
        conexao.putheader("Authorization", f"Bearer {token}")
        conexao.endheaders()
        assert conexao.getresponse().status == 401
    finally:
        conexao.close()


def test_cabecalho_gigante_e_401_e_o_servidor_segue_no_ar(com_token):
    token, servidor, _ = com_token
    gigante = {"Authorization": "Bearer " + token + "A" * 60_000}
    status, _, corpo = _pede(servidor.porta, "GET", "/estado", gigante)
    assert status == 401
    assert b"A" * 100 not in corpo
    assert _pede(servidor.porta, "GET", "/estado", _bearer(token))[0] == 200


def test_cookie_gigante_e_401_e_o_servidor_segue_no_ar(com_token):
    token, servidor, _ = com_token
    gigante = {"Cookie": f"{COOKIE_SESSAO}=" + "A" * 60_000}
    assert _pede(servidor.porta, "GET", "/estado", gigante)[0] == 401
    assert _pede(servidor.porta, "GET", "/estado", _bearer(token))[0] == 200


def test_healthz_fica_livre_com_token(com_token):
    _, servidor, _ = com_token
    status, _, corpo = _pede(servidor.porta, "GET", "/healthz")
    assert (status, corpo) == (200, b'{"status":"ok"}')


def test_healthz_continua_barrando_host_estranho(com_token):
    _, servidor, _ = com_token
    status, _, _ = _pede(servidor.porta, "GET", "/healthz", {"Host": "evil.example"})
    assert status == 421


def test_host_estranho_com_token_certo_continua_421(com_token):
    """A validação de Host vem antes do token: rebinding não vira pedido válido."""
    token, servidor, _ = com_token
    cabecalhos = {**_bearer(token), "Host": f"evil.example:{servidor.porta}"}
    assert _pede(servidor.porta, "GET", "/estado", cabecalhos)[0] == 421


# --- com token: escrita ----------------------------------------------------


@pytest.mark.parametrize("rota", ["/ancora", "/dono"])
def test_com_token_escrita_sem_cabecalho_e_401_e_nao_toca_a_sessao(com_token, rota):
    token, servidor, sessao = com_token
    antes = len(sessao.anchors())
    status, cabecalhos, corpo = _pede(
        servidor.porta,
        "POST",
        rota,
        {"Content-Type": "application/json"},
        _json({"x": 0.525, "y": 0.5, "bagagem": 1}),
    )
    assert status == 401
    assert cabecalhos["WWW-Authenticate"].startswith("Bearer")
    assert token.encode() not in corpo
    assert len(sessao.anchors()) == antes


def test_com_token_escrita_com_bearer_aponta(com_token):
    token, servidor, sessao = com_token
    cabecalhos = {**_bearer(token), "Content-Type": "application/json"}
    status, _, corpo = _pede(
        servidor.porta, "POST", "/ancora", cabecalhos, _json({"x": 0.525, "y": 0.5})
    )
    assert status == 200
    bagagem = json.loads(corpo)["bagagem"]
    assert any(b.bag_id == bagagem for b in sessao.anchors())


def test_com_token_escrita_com_token_errado_e_401(com_token):
    _, servidor, sessao = com_token
    cabecalhos = {**_bearer(_token()), "Content-Type": "application/json"}
    status, _, _ = _pede(
        servidor.porta, "POST", "/ancora", cabecalhos, _json({"x": 0.525, "y": 0.5})
    )
    assert status == 401
    assert sessao.anchors() == []


def test_401_com_corpo_chega_inteiro_ao_cliente(com_token):
    """Recusa por token com corpo pendente: drena antes de fechar, como #129."""
    _, servidor, _ = com_token
    corpo = b"x" * 2048
    recebidos = [
        _pede(servidor.porta, "POST", "/ancora", {"Content-Type": "application/json"}, corpo)[0]
        for _ in range(REPETICOES)
    ]
    assert recebidos == [401] * REPETICOES


def test_conexao_reaproveitada_sobrevive_ao_401(com_token):
    """Um cliente que reaproveita a conexão depois do 401 não perde o pedido
    seguinte: ou a mesma conexão atende, ou ele reabre sem erro."""
    token, servidor, _ = com_token
    conexao = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5.0)
    try:
        for cabecalhos, esperado in ((None, 401), (_bearer(token), 200), (None, 401)):
            conexao.request("GET", "/estado", headers=cabecalhos or {})
            resposta = conexao.getresponse()
            resposta.read()
            assert resposta.status == esperado
    finally:
        conexao.close()


# --- cookie de sessão ------------------------------------------------------


def _entra(porta: int, token: str) -> tuple[int, http.client.HTTPMessage]:
    status, cabecalhos, _ = _pede(porta, "POST", "/sessao", _bearer(token))
    return status, cabecalhos


def _cookie(cabecalhos: http.client.HTTPMessage) -> str:
    return cabecalhos["Set-Cookie"].split(";", 1)[0]


def test_sessao_troca_token_por_cookie_httponly_samesite(com_token):
    token, servidor, _ = com_token
    status, cabecalhos = _entra(servidor.porta, token)
    assert status == 204
    atributos = [a.strip() for a in cabecalhos["Set-Cookie"].split(";")]
    nome, valor = atributos[0].split("=", 1)
    assert nome == COOKIE_SESSAO
    assert valor and token not in valor
    assert "HttpOnly" in atributos
    assert "SameSite=Strict" in atributos
    assert "Path=/" in atributos
    assert any(a.startswith("Max-Age=") for a in atributos)
    assert "Secure" not in atributos  # HTTP em loopback: o navegador descartaria
    assert cabecalhos["Cache-Control"] == "no-store"


def test_cookie_da_sessao_abre_leitura_e_escrita(com_token):
    token, servidor, sessao = com_token
    _, cabecalhos = _entra(servidor.porta, token)
    cookie = {"Cookie": _cookie(cabecalhos)}
    for rota in ("/", "/estado", "/quadro.jpg"):
        assert _pede(servidor.porta, "GET", rota, cookie)[0] == 200
    status, _, _ = _pede(
        servidor.porta,
        "POST",
        "/ancora",
        {**cookie, "Content-Type": "application/json"},
        _json({"x": 0.525, "y": 0.5}),
    )
    assert status == 200
    assert len(sessao.anchors()) == 1


def test_cookie_entre_outros_cookies_vale(com_token):
    token, servidor, _ = com_token
    _, cabecalhos = _entra(servidor.porta, token)
    cookie = {"Cookie": f"tema=escuro; {_cookie(cabecalhos)}; outro=1"}
    assert _pede(servidor.porta, "GET", "/estado", cookie)[0] == 200


def test_cookie_forjado_e_401(com_token):
    _, servidor, _ = com_token
    forjado = {"Cookie": f"{COOKIE_SESSAO}={secrets.token_urlsafe(32)}"}
    assert _pede(servidor.porta, "GET", "/estado", forjado)[0] == 401


def test_cookie_com_o_proprio_token_e_401(com_token):
    """O cookie carrega um id de sessão aleatório, nunca o token."""
    token, servidor, _ = com_token
    assert _pede(servidor.porta, "GET", "/estado", {"Cookie": f"{COOKIE_SESSAO}={token}"})[0] == 401


def test_sessao_exige_bearer_e_nao_renova_por_cookie(com_token):
    token, servidor, _ = com_token
    _, cabecalhos = _entra(servidor.porta, token)
    assert _pede(servidor.porta, "POST", "/sessao", {"Cookie": _cookie(cabecalhos)})[0] == 401
    assert _entra(servidor.porta, _token())[0] == 401
    assert _pede(servidor.porta, "POST", "/sessao")[0] == 401


def test_sessao_com_token_nao_aceita_get(com_token):
    token, servidor, _ = com_token
    status, cabecalhos, _ = _pede(servidor.porta, "GET", "/sessao", _bearer(token))
    assert status == 404
    assert cabecalhos["Set-Cookie"] is None


def test_cookie_com_tls_leva_secure():
    assert "; Secure" in cabecalho_cookie("abc", cifrado=True)
    assert "Secure" not in cabecalho_cookie("abc", cifrado=False)


def test_sessoes_expiram_e_tem_teto():
    agora = [0.0]
    sessoes = Sessoes(duracao_s=10.0, maximo=2, relogio=lambda: agora[0])
    a = sessoes.cria()
    assert sessoes.valida(a)
    agora[0] = 10.5
    assert not sessoes.valida(a)
    b, c, d = sessoes.cria(), sessoes.cria(), sessoes.cria()
    assert not sessoes.valida(b)  # a mais antiga sai quando passa do teto
    assert sessoes.valida(c) and sessoes.valida(d)
    assert not sessoes.valida("")


# --- comparação em tempo constante -----------------------------------------


def test_comparacao_usa_compare_digest_sobre_bytes(com_token, monkeypatch):
    token, servidor, _ = com_token
    chamadas = []
    original = hmac.compare_digest

    def espia(a, b):
        chamadas.append((type(a), type(b)))
        return original(a, b)

    monkeypatch.setattr(hmac, "compare_digest", espia)
    assert _pede(servidor.porta, "GET", "/estado", _bearer(token))[0] == 200
    assert _pede(servidor.porta, "GET", "/estado", _bearer(_token()))[0] == 401
    assert chamadas
    assert all(tipos == (bytes, bytes) for tipos in chamadas)


# --- o token não aparece em saída nenhuma ----------------------------------


def test_token_nunca_aparece_em_resposta_nem_no_terminal(com_token, capsys):
    token, servidor, _ = com_token
    pedidos = [
        ("GET", "/", {}),
        ("GET", "/estado", _bearer(token)),
        ("GET", "/estado", _bearer(token + "x")),
        ("GET", "/estado", {"Authorization": "Bearer " + token * 300}),
        ("POST", "/sessao", _bearer(token)),
        ("POST", "/ancora", {**_bearer(token), "Content-Type": "application/json"}),
        ("PUT", "/estado", _bearer(token)),
        ("GET", "/nao-existe", _bearer(token)),
    ]
    for metodo, rota, cabecalhos in pedidos:
        _, resposta_cabecalhos, corpo = _pede(servidor.porta, metodo, rota, cabecalhos, b"{}")
        assert token.encode() not in corpo
        assert token not in str(resposta_cabecalhos)
    saida = capsys.readouterr()
    assert token not in saida.out
    assert token not in saida.err


# --- subida ----------------------------------------------------------------


@pytest.mark.parametrize(
    "ruim",
    [
        "",
        "a" * (TOKEN_MIN - 1),
        "a" * TOKEN_MIN,  # comprimento certo, diversidade nenhuma
        "ab" * TOKEN_MIN,
        "x" * (TOKEN_MAX + 1),
        "senha com espaço " * 3,
        "ç" * TOKEN_MIN + "abcdefgh",
    ],
)
def test_token_fraco_e_recusado_na_subida_sem_ecoar(ruim):
    with pytest.raises(ValueError) as erro:
        Servidor(Painel(), "127.0.0.1", 0, token=ruim)
    mensagem = str(erro.value)
    assert TOKEN_ENV in mensagem
    if ruim:
        assert ruim not in mensagem
        assert ruim[:8] not in mensagem


def test_token_valido_aceita_o_gerado_e_recusa_fraco():
    assert token_valido(_token())
    assert token_valido(secrets.token_hex(TOKEN_MAX // 2))
    assert not token_valido("0" * 40)


def test_fora_do_loopback_sem_token_e_recusado():
    """TLS sozinho não identifica cliente: na rede, sem token, qualquer um
    que alcance a porta assiste a câmera e escreve na custódia."""
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with pytest.raises(ValueError, match=TOKEN_ENV):
        Servidor(Painel(), "0.0.0.0", 0, tls=tls)


def test_fora_do_loopback_sem_tls_continua_pedindo_cert_primeiro():
    with pytest.raises(ValueError, match="--cert"):
        Servidor(Painel(), "192.168.0.10", 0, token=_token())
