"""Exportador por webhook: lê o journal confirmado, assina e entrega at-least-once."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from custody_watch import cli
from custody_watch.event_journal import JournalWriter
from custody_watch.events import Event, EventKind
from custody_watch.webhook_export import (
    EVENT_ID_HEADER,
    SECRET_ENV,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    ExportConfig,
    WebhookConfigError,
    WebhookExporter,
    build_payload,
    load_cursor,
    load_secret,
    sign_body,
    validate_destination,
    verify_signature,
)

SEGREDO = b"s" * 40
AGORA = 1_700_000_000


class Destino:
    """Servidor HTTP local em thread; cada requisição consome uma ação do roteiro."""

    def __init__(self, roteiro=(), padrao=200, location=None):
        self.roteiro = list(roteiro)
        self.padrao = padrao
        self.location = location
        self.recebidos = []
        destino = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                corpo = self.rfile.read(int(self.headers["Content-Length"]))
                destino.recebidos.append({"headers": dict(self.headers), "corpo": corpo})
                acao = destino.roteiro.pop(0) if destino.roteiro else destino.padrao
                if isinstance(acao, tuple):
                    time.sleep(acao[1])
                    acao = 200
                self.send_response(acao)
                if 300 <= acao < 400 and destino.location:
                    self.send_header("Location", destino.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.servidor.daemon_threads = True
        self.servidor.handle_error = lambda *args: None
        self.thread = threading.Thread(target=self.servidor.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.servidor.server_address[1]}/hook"

    def eventos(self):
        return [json.loads(item["corpo"]) for item in self.recebidos]

    def fechar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


@pytest.fixture
def destinos():
    abertos = []

    def criar(*args, **kwargs):
        destino = Destino(*args, **kwargs)
        abertos.append(destino)
        return destino

    yield criar
    for destino in abertos:
        destino.fechar()


def evento(bag, **evidence):
    return Event(EventKind.BAG_UNATTENDED, 1.0, 2.0 + bag, None, bag, 7, evidence)


@pytest.fixture(autouse=True)
def diretorio_de_trabalho(tmp_path, monkeypatch):
    """O cursor só é aceito dentro do diretório de trabalho do processo."""
    monkeypatch.chdir(tmp_path)


def journal(tmp_path, quantidade, *, por_parte=10000, fechar=True):
    writer = JournalWriter(
        tmp_path / "journal", session_id="sessao", max_records_per_file=por_parte
    )
    for bag in range(quantidade):
        assert writer.emit(evento(bag, distance_m=3.5, elapsed_s=30.0))
    if fechar:
        writer.close()
    return writer


def exportador(tmp_path, url, **config):
    pausas = []
    exporter = WebhookExporter(
        tmp_path / "journal" / "sessao",
        tmp_path / "cursor.json",
        ExportConfig(url=url, secret=SEGREDO, **{"timeout_s": 2.0, **config}),
        sleep=pausas.append,
        wall_clock=lambda: AGORA,
        random=lambda: 0.5,
    )
    return exporter, pausas


# --- destino e segredo -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://vms.example.org/hook",
        "http://127.0.0.1:8080/hook",
        "http://localhost/hook",
        "http://[::1]:9000/",
        "HTTPS://vms.example.org:8443/a/b",
    ],
)
def test_destino_valido(url):
    assert validate_destination(url) == url


@pytest.mark.parametrize(
    ("url", "trecho"),
    [
        ("ftp://vms.example.org/hook", "esquema"),
        ("file:///etc/passwd", "esquema"),
        ("http://vms.example.org/hook", "https"),
        ("http://10.0.0.5/hook", "https"),
        ("https://operador:SENHA123@vms.example.org/hook", "credencial"),
        ("https://vms.example.org/hook?token=SENHA123", "query"),
        ("https://vms.example.org/hook#SENHA123", "fragmento"),
        ("https:///hook", "host"),
        ("https://vms.example.org:99999/hook", "porta"),
        ("https://vms.example.org/ho ok", "caractere"),
    ],
)
def test_destino_invalido_recusado_sem_ecoar(url, trecho):
    with pytest.raises(WebhookConfigError) as erro:
        validate_destination(url)
    mensagem = str(erro.value)
    assert trecho in mensagem
    assert "SENHA123" not in mensagem
    assert "vms.example.org" not in mensagem


def test_segredo_so_por_ambiente():
    assert load_secret({SECRET_ENV: "x" * 32}) == b"x" * 32
    with pytest.raises(WebhookConfigError, match=SECRET_ENV):
        load_secret({})
    with pytest.raises(WebhookConfigError) as erro:
        load_secret({SECRET_ENV: "curtoDEMAIS"})
    assert "curtoDEMAIS" not in str(erro.value)


def test_config_nao_expoe_segredo_no_repr():
    config = ExportConfig(url="https://vms.example.org/hook", secret=SEGREDO)
    assert SEGREDO.decode() not in repr(config)


def test_config_recusa_segredo_fraco_e_limites_invalidos():
    with pytest.raises(WebhookConfigError):
        ExportConfig(url="https://vms.example.org/hook", secret=b"curto")
    with pytest.raises(WebhookConfigError):
        ExportConfig(url="https://vms.example.org/hook", secret=SEGREDO, max_attempts=0)
    with pytest.raises(WebhookConfigError):
        ExportConfig(url="https://vms.example.org/hook", secret=SEGREDO, timeout_s=0)


# --- assinatura e carga ----------------------------------------------------


def test_assinatura_cobre_timestamp_e_corpo():
    corpo = b'{"id":"sessao:1"}'
    assinatura = sign_body(SEGREDO, AGORA, corpo)
    assert assinatura.startswith("sha256=")
    assert verify_signature(SEGREDO, str(AGORA), corpo, assinatura, now=AGORA + 10)
    assert not verify_signature(SEGREDO, str(AGORA + 1), corpo, assinatura, now=AGORA)
    assert not verify_signature(SEGREDO, str(AGORA), corpo + b" ", assinatura, now=AGORA)
    assert not verify_signature(b"o" * 40, str(AGORA), corpo, assinatura, now=AGORA)
    # Replay fora da janela de tolerância é recusado mesmo com assinatura válida.
    assert not verify_signature(SEGREDO, str(AGORA), corpo, assinatura, now=AGORA + 3600)


def test_carga_so_com_campos_permitidos():
    registro = {
        "schema_version": 1,
        "session_id": "sessao",
        "segment_id": "seg",
        "sequence": 4,
        "kind": "event",
        "payload": {
            **Event(
                EventKind.BAG_REMOVED_BY_STRANGER,
                1.0,
                2.0,
                9,
                -3,
                1,
                {
                    "carrier": 9,
                    "owner_party": 1,
                    "anchor": [1.5, 2.0],
                    "bond": "strong",
                    "candidate_people": [9, 10],
                    "frame_path": "C:/Users/fulano/frame.jpg",
                    "crop_png": "iVBORw0KGgo=",
                    "origem": "C:/segredo",
                    "distance_m": "3",
                    "members": [1, "x"],
                    "nested": {"a": 1},
                },
            ).to_dict(),
            "thumbnail": "data:image/png;base64,AAAA",
        },
    }
    carga = build_payload(registro)
    assert carga["id"] == "sessao:4"
    assert carga["event"] == {
        "kind": "bag_removed_by_stranger",
        "t_start": 1.0,
        "t_end": 2.0,
        "subject": 9,
        "bag": -3,
        "party": 1,
        "evidence": {
            "carrier": 9,
            "owner_party": 1,
            "anchor": [1.5, 2.0],
            "bond": "strong",
            "candidate_people": [9, 10],
        },
    }
    texto = json.dumps(carga)
    for vazamento in ("frame_path", "fulano", "crop_png", "thumbnail", "segredo", "nested"):
        assert vazamento not in texto


# --- entrega com servidor local --------------------------------------------


def test_sucesso_entrega_eventos_assinados_em_ordem(tmp_path, destinos):
    destino = destinos()
    journal(tmp_path, 3)
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    eventos = destino.eventos()
    assert [item["id"] for item in eventos] == ["sessao:1", "sessao:2", "sessao:3"]
    assert [item["event"]["bag"] for item in eventos] == [0, 1, 2]
    for item in destino.recebidos:
        headers = item["headers"]
        assert headers[EVENT_ID_HEADER] == json.loads(item["corpo"])["id"]
        assert verify_signature(
            SEGREDO, headers[TIMESTAMP_HEADER], item["corpo"], headers[SIGNATURE_HEADER], now=AGORA
        )
    assert exporter.stats.sent == 3
    assert exporter.stats.skipped_records == 2  # segment_start e segment_end
    # Sem eventos novos, uma segunda passada não reenvia nada.
    assert exporter.run_once() == "caught_up"
    assert len(destino.recebidos) == 3


def test_timeout_e_retentado(tmp_path, destinos):
    destino = destinos([("dorme", 1.0)])
    journal(tmp_path, 1)
    exporter, pausas = exportador(tmp_path, destino.url, timeout_s=0.2)
    assert exporter.run_once() == "caught_up"
    assert exporter.stats.sent == 1
    assert exporter.stats.attempts == 2
    assert exporter.stats.retries == 1
    assert len(pausas) == 1


def test_5xx_seguido_de_sucesso_com_backoff_limitado(tmp_path, destinos):
    destino = destinos([500, 502, 503, 504])
    journal(tmp_path, 1)
    exporter, pausas = exportador(
        tmp_path, destino.url, max_attempts=5, backoff_base_s=1.0, backoff_max_s=3.0, jitter=0.5
    )
    assert exporter.run_once() == "caught_up"
    assert exporter.stats.sent == 1
    assert exporter.stats.attempts == 5
    # base*2**n limitado a 3 s; jitter com random()=0.5 reduz 25%.
    assert pausas == [0.75, 1.5, 2.25, 2.25]
    ids = {item["id"] for item in destino.eventos()}
    assert ids == {"sessao:1"}  # reenvios levam o mesmo id para deduplicar


@pytest.mark.parametrize("status", [408, 429])
def test_4xx_transitorio_e_retentado(tmp_path, destinos, status):
    destino = destinos([status])
    journal(tmp_path, 1)
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert exporter.stats.sent == 1
    assert exporter.stats.discarded == 0


def test_4xx_descarta_contando_e_segue(tmp_path, destinos):
    destino = destinos([400, 422])
    journal(tmp_path, 3)
    exporter, pausas = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert exporter.stats.discarded == 2
    assert exporter.stats.sent == 1
    assert exporter.stats.attempts == 3
    assert pausas == []
    assert load_cursor(tmp_path / "cursor.json", "sessao").next_sequence == 5


@pytest.mark.parametrize("status", [401, 403, 404])
def test_erro_de_configuracao_do_destino_para_sem_perder(tmp_path, destinos, status):
    destino = destinos([status])
    journal(tmp_path, 2)
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "stalled"
    assert exporter.stats.discarded == 0
    assert exporter.stats.attempts == 1
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert [item["id"] for item in destino.eventos()] == ["sessao:1", "sessao:1", "sessao:2"]


def test_redirecionamento_nao_e_seguido(tmp_path, destinos):
    alvo = destinos()
    origem = destinos([302], location=alvo.url)
    journal(tmp_path, 1)
    exporter, _ = exportador(tmp_path, origem.url)
    assert exporter.run_once() == "stalled"
    assert alvo.recebidos == []
    assert exporter.stats.sent == 0
    assert exporter.stats.discarded == 0


def test_tentativas_esgotadas_param_sem_avancar_e_retomam_apos_reinicio(tmp_path, destinos):
    fora = destinos(padrao=503)
    journal(tmp_path, 2)
    exporter, _ = exportador(tmp_path, fora.url, max_attempts=3)
    assert exporter.run_once() == "stalled"
    assert exporter.stats.attempts == 3
    assert exporter.stats.stalled == 1
    assert exporter.stats.sent == 0
    # Processo novo, destino saudável: nada se perdeu.
    destino = destinos()
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert [item["id"] for item in destino.eventos()] == ["sessao:1", "sessao:2"]


def test_retomada_apos_reinicio_nao_reenvia_o_que_foi_confirmado(tmp_path, destinos):
    destino = destinos([200, 200, 503], padrao=503)
    journal(tmp_path, 3)
    exporter, _ = exportador(tmp_path, destino.url, max_attempts=1)
    assert exporter.run_once() == "stalled"
    assert exporter.stats.sent == 2
    destino.padrao = 200
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert [item["id"] for item in destino.eventos()] == [
        "sessao:1",
        "sessao:2",
        "sessao:3",
        "sessao:3",
    ]


def test_partes_rotacionadas_sao_seguidas(tmp_path, destinos):
    destino = destinos()
    journal(tmp_path, 5, por_parte=2)
    assert (tmp_path / "journal" / "sessao" / "000003.jsonl").exists()
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert [item["event"]["bag"] for item in destino.eventos()] == [0, 1, 2, 3, 4]


def test_journal_vivo_e_acompanhado_e_pipeline_nao_espera_destino(tmp_path, destinos):
    """Destino fora não bloqueia o escritor; o backlog fica no journal e é drenado."""
    fora = destinos(padrao=503)
    writer = journal(tmp_path, 0, por_parte=50, fechar=False)
    exporter, _ = exportador(tmp_path, fora.url, max_attempts=1)
    for bag in range(200):
        assert writer.emit(evento(bag))
        if bag % 40 == 0:
            assert exporter.run_once() == "stalled"
    assert writer.snapshot()["unconfirmed_events"] == 0
    writer.close()
    destino = destinos()
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert [item["event"]["bag"] for item in destino.eventos()] == list(range(200))


def test_linha_parcial_no_fim_nao_e_enviada(tmp_path, destinos):
    destino = destinos()
    journal(tmp_path, 1, fechar=False)
    parte = tmp_path / "journal" / "sessao" / "000000.jsonl"
    completa = json.dumps(
        {
            "schema_version": 1,
            "session_id": "sessao",
            "segment_id": json.loads(parte.read_text().splitlines()[0])["segment_id"],
            "sequence": 2,
            "kind": "event",
            "payload": evento(42).to_dict(),
        }
    ).encode()
    with parte.open("ab") as handle:
        handle.write(completa[:30])
    exporter, _ = exportador(tmp_path, destino.url)
    assert exporter.run_once() == "caught_up"
    assert [item["event"]["bag"] for item in destino.eventos()] == [0]
    assert exporter.stats.invalid_records == 0
    with parte.open("ab") as handle:
        handle.write(completa[30:] + b"\n")
    assert exporter.run_once() == "caught_up"
    assert [item["event"]["bag"] for item in destino.eventos()] == [0, 42]


def test_linha_gigante_e_malformada_sao_descartadas_sem_travar(tmp_path, destinos):
    destino = destinos()
    journal(tmp_path, 1, fechar=False)
    parte = tmp_path / "journal" / "sessao" / "000000.jsonl"
    segmento = json.loads(parte.read_text().splitlines()[0])["segment_id"]

    def linha(sequence, bag):
        return (
            json.dumps(
                {
                    "schema_version": 1,
                    "session_id": "sessao",
                    "segment_id": segmento,
                    "sequence": sequence,
                    "kind": "event",
                    "payload": evento(bag).to_dict(),
                }
            )
            + "\n"
        ).encode()

    with parte.open("ab") as handle:
        handle.write(b'{"a":"' + b"x" * 5_000_000 + b'"}\n')  # acima do limite
        handle.write(b"[" * 40_000 + b"\n")  # recursão profunda
        handle.write(b"\xff\xfe nao e utf-8\n")
        handle.write(b'{"schema_version": 1, "kind": "event"}\n')  # envelope inválido
        handle.write(linha(1, 99))  # sequence repetida: replay recusado
        handle.write(linha(7, 8))
    exporter, _ = exportador(tmp_path, destino.url, max_line_bytes=65536)
    inicio = time.monotonic()
    assert exporter.run_once() == "caught_up"
    assert time.monotonic() - inicio < 10
    assert [item["event"]["bag"] for item in destino.eventos()] == [0, 8]
    assert exporter.stats.invalid_records == 5


def test_linha_gigante_incompleta_no_fim_espera_sem_avancar(tmp_path, destinos):
    destino = destinos()
    journal(tmp_path, 1, fechar=False)
    parte = tmp_path / "journal" / "sessao" / "000000.jsonl"
    with parte.open("ab") as handle:
        handle.write(b"y" * 300_000)
    exporter, _ = exportador(tmp_path, destino.url, max_line_bytes=1024)
    assert exporter.run_once() == "caught_up"
    assert exporter.stats.invalid_records == 0
    cursor = load_cursor(tmp_path / "cursor.json", "sessao")
    assert cursor.offset < parte.stat().st_size


def test_cursor_corrompido_ou_de_outra_sessao_e_recusado(tmp_path):
    cursor = tmp_path / "cursor.json"
    cursor.write_text("{nao json")
    with pytest.raises(WebhookConfigError):
        load_cursor(cursor, "sessao")
    cursor.write_text(
        json.dumps(
            {"version": 1, "session_id": "outra", "part": 0, "offset": 0, "next_sequence": 0}
        )
    )
    with pytest.raises(WebhookConfigError):
        load_cursor(cursor, "sessao")
    cursor.write_text(
        json.dumps(
            {"version": 1, "session_id": "sessao", "part": -1, "offset": 0, "next_sequence": 0}
        )
    )
    with pytest.raises(WebhookConfigError):
        load_cursor(cursor, "sessao")
    assert load_cursor(tmp_path / "ausente.json", "sessao").next_sequence == 0


@pytest.mark.parametrize("nome", ["000009.jsonl", "sem-pasta/cursor.json"])
def test_cursor_em_local_perigoso_ou_inexistente_e_recusado(tmp_path, nome):
    journal(tmp_path, 1)
    config = ExportConfig(url="http://127.0.0.1:9/hook", secret=SEGREDO)
    with pytest.raises(WebhookConfigError):
        WebhookExporter(tmp_path / "journal" / "sessao", tmp_path / nome, config)


@pytest.mark.parametrize("nome", ["../cursor.json", "estado/../../cursor.json"])
def test_cursor_fora_do_diretorio_de_trabalho_e_recusado(tmp_path, monkeypatch, nome):
    journal(tmp_path, 1)
    (tmp_path / "estado").mkdir()
    (tmp_path / "trabalho").mkdir()
    monkeypatch.chdir(tmp_path / "trabalho")
    config = ExportConfig(url="http://127.0.0.1:9/hook", secret=SEGREDO)
    with pytest.raises(WebhookConfigError, match="diretório de trabalho"):
        WebhookExporter(tmp_path / "journal" / "sessao", nome, config)
    with pytest.raises(WebhookConfigError, match="diretório de trabalho"):
        WebhookExporter(tmp_path / "journal" / "sessao", tmp_path / "cursor.json", config)
    assert not (tmp_path / "cursor.json").exists()


def test_cursor_relativo_fica_dentro_do_diretorio_de_trabalho(tmp_path, destinos):
    destino = destinos()
    journal(tmp_path, 1)
    (tmp_path / "estado").mkdir()
    config = ExportConfig(url=destino.url, secret=SEGREDO, timeout_s=2.0)
    exporter = WebhookExporter(tmp_path / "journal" / "sessao", "estado/cursor.json", config)
    assert exporter.run_once() == "caught_up"
    salvo = load_cursor(tmp_path / "estado" / "cursor.json", "sessao")
    assert salvo == exporter.cursor
    assert salvo.offset > 0


def test_contadores_em_json_sem_conteudo_de_evento(tmp_path, destinos):
    destino = destinos([400])
    journal(tmp_path, 2)
    exporter, _ = exportador(tmp_path, destino.url)
    exporter.run_once()
    linha = exporter.stats.to_json_line()
    assert "\n" not in linha
    contadores = json.loads(linha)
    assert contadores["sent"] == 1
    assert contadores["discarded"] == 1
    assert contadores["attempts"] == 2
    assert {"retries", "invalid_records", "stalled", "max_delivery_s"} <= contadores.keys()
    for vazamento in ("bag_unattended", "distance_m", "sessao", "evidence"):
        assert vazamento not in linha


# --- script ----------------------------------------------------------------


def test_script_recusa_segredo_ausente_e_url_invalida(tmp_path, monkeypatch, capsys):
    journal(tmp_path, 1)
    sessao = str(tmp_path / "journal" / "sessao")
    cursor = str(tmp_path / "cursor.json")
    monkeypatch.delenv(SECRET_ENV, raising=False)
    assert cli.main(["webhook", sessao, "--url", "https://vms.example.org/h", "--cursor", cursor])
    assert SECRET_ENV in capsys.readouterr().err
    monkeypatch.setenv(SECRET_ENV, "Z" * 40)
    codigo = cli.main(
        ["webhook", sessao, "--url", "https://u:SENHA123@vms.example.org/h", "--cursor", cursor]
    )
    saida = capsys.readouterr()
    assert codigo == 2
    assert "SENHA123" not in saida.err + saida.out
    assert "Z" * 40 not in saida.err + saida.out


def test_script_once_entrega_e_imprime_contadores(tmp_path, destinos, monkeypatch, capsys):
    destino = destinos()
    journal(tmp_path, 2)
    monkeypatch.setenv(SECRET_ENV, "Z" * 40)
    codigo = cli.main(
        [
            "webhook",
            str(tmp_path / "journal" / "sessao"),
            "--url",
            destino.url,
            "--cursor",
            str(tmp_path / "cursor.json"),
            "--once",
        ]
    )
    assert codigo == 0
    assert len(destino.recebidos) == 2
    ultima = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(ultima)["sent"] == 2
    assert "Z" * 40 not in ultima
