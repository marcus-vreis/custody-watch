import numpy as np

from custody_watch.config import Config, CustodyConfig, PipelineConfig
from custody_watch.events import EventKind
from custody_watch.ground_plane import GroundPlane
from custody_watch.orchestrator import run_session
from custody_watch.tracking import TrackedDetection
from custody_watch.types import FlagLevel

PLANO = GroundPlane(np.eye(3))

# Nos testes o merge roda a cada frame: esperar 25 quadros só alongaria os
# cenários sem exercitar nada. E `cena` faz um quadro valer um segundo, então
# o timeout de oclusão desce para o mínimo da faixa segura — senão todo
# cenário de retirada precisaria de mais de trinta quadros.
RAPIDO = Config(
    pipeline=PipelineConfig(merge_every_frames=1),
    custody=CustodyConfig(max_occlusion_s=5.0),
)


def pessoa(track_id: int, x: float) -> TrackedDetection:
    return TrackedDetection(track_id=track_id, cls="person", bbox=(x - 0.5, 0.0, x + 0.5, 1.0))


def mala(track_id: int, x: float) -> TrackedDetection:
    return TrackedDetection(track_id=track_id, cls="suitcase", bbox=(x - 0.2, 0.0, x + 0.2, 0.4))


def cena(*frames: list[TrackedDetection]):
    return ((float(i), frame) for i, frame in enumerate(frames))


def casal_andando(passos: int = 6) -> list[list[TrackedDetection]]:
    """Duas pessoas cobrindo terreno lado a lado, dentro de 2m uma da outra."""
    return [[pessoa(1, 2.0 * i), pessoa(2, 2.0 * i + 0.5)] for i in range(passos)]


# --- formação de grupo --------------------------------------------------------


def test_ninguem_ganha_grupo_so_por_aparecer():
    """Grupo de um na chegada bloqueava toda fusão.

    `try_join_strong` recusa migração entre grupos e `join_weak` recusa quem já
    tem grupo, então afiliar todo mundo de saída fazia as duas guardas
    rejeitarem tudo.
    """
    resultado = run_session(cena([pessoa(1, 0.0), pessoa(2, 10.0)]), PLANO, RAPIDO)

    assert resultado.events.of_kind(EventKind.PARTY_FORMED) == []


def test_grupo_nasce_quando_alguem_precisa_ter_posse():
    """A bagagem precisa ficar parada antes de virar âncora, então o cenário
    tem os quadros do depósito -- sem eles não há posse de que o grupo possa
    nascer."""
    quadros = [[pessoa(1, 0.0)]] + [[pessoa(1, 0.0), mala(9, 0.5)]] * 4

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.PARTY_FORMED)) == 1
    assert len(resultado.events.of_kind(EventKind.BAG_OWNED)) == 1


def test_co_movimento_sustentado_funde_dois_tracks():
    """O mecanismo que existia, era testado, e o pipeline não usava."""
    resultado = run_session(cena(*casal_andando()), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.PARTY_JOINED_STRONG)) == 1


def test_pessoas_distantes_nao_sao_fundidas():
    quadros = [[pessoa(1, 2.0 * i), pessoa(2, 2.0 * i + 50.0)] for i in range(6)]

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert resultado.events.of_kind(EventKind.PARTY_JOINED_STRONG) == []


# --- o caso que motivou o sistema de grupos -----------------------------------


def test_companheiro_recolhe_a_mala_e_retirada_legitima():
    """A maior fonte de falso positivo do projeto, agora tratada de fato.

    O casal anda junto, um deposita a mala e sai, o outro a recolhe. Sem fusão
    de grupos isso vira acusação de furto.
    """
    quadros = casal_andando()
    quadros += [[pessoa(1, 10.0), pessoa(2, 10.5), mala(9, 10.2)]] * 3
    # A pessoa 1 sai antes de a mala sumir, para que o carregador seja
    # inequivocamente a 2 e o teste dependa mesmo da fusão de grupos.
    quadros += [[pessoa(2, 10.3), mala(9, 10.2)]] * 3
    # A retirada agora resolve no timeout de oclusão, não no desaparecimento:
    # os quadros restantes precisam ultrapassar missing_frames_before_occluded
    # + max_occlusion_s de RAPIDO, senão a bagagem nunca sai do estado adiado.
    quadros += [[pessoa(2, 10.3)]] * 16

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_OWNER)) == 1
    assert resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER) == []
    assert resultado.queue == []


def test_estranho_recolhe_a_mala_do_casal_e_alerta():
    """O espelho: fundir grupos não pode cegar o sistema para furto."""
    quadros = casal_andando()
    quadros += [[pessoa(1, 10.0), pessoa(2, 10.5), mala(9, 10.2)]] * 3
    # Mesma razão do teste anterior: precisa sobreviver ao timeout de oclusão.
    quadros += [[pessoa(7, 10.2)]] * 16

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER)) == 1
    assert [item.person for item in resultado.queue] == [7]


# --- flags relacionais --------------------------------------------------------


def test_contato_com_bagagem_alheia_gera_flag_n2():
    quadros = [[pessoa(1, 0.0), mala(9, 0.3)]] * 4
    quadros += [[pessoa(1, 0.0), pessoa(7, 0.3), mala(9, 0.3)]] * 2

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    flags = resultado.flags.for_person(7)
    assert [f.level for f in flags] == [FlagLevel.N2]
    assert "bagagem 9" in flags[0].explanation


def test_contato_e_flagrado_uma_vez_so():
    """Sem deduplicação, ficar ao lado da mala geraria um flag por frame."""
    quadros = [[pessoa(1, 0.0), mala(9, 0.3)]] * 4
    quadros += [[pessoa(1, 0.0), pessoa(7, 0.3), mala(9, 0.3)]] * 30

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.flags.for_person(7)) == 1


def test_dono_perto_da_propria_mala_nao_gera_flag():
    """Regra P4: o flag descreve relação com bagagem ALHEIA."""
    quadros = [[pessoa(1, 0.0), mala(9, 0.3)]] * 20

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert resultado.flags.for_person(1) == []


def test_permanencia_prolongada_gera_flag_n1():
    config = Config(pipeline=PipelineConfig(merge_every_frames=1, proximity_flag_s=3.0))
    quadros = [[pessoa(1, 0.0), mala(9, 0.3)]] * 2
    quadros += [[pessoa(1, 0.0), pessoa(7, 1.5), mala(9, 0.3)]] * 8

    resultado = run_session(cena(*quadros), PLANO, config)

    flags = resultado.flags.for_person(7)
    assert [f.level for f in flags] == [FlagLevel.N1]


def test_n1_sozinho_nao_entra_na_fila():
    """N1 acumula contexto sem consumir tempo de operador."""
    config = Config(pipeline=PipelineConfig(merge_every_frames=1, proximity_flag_s=3.0))
    quadros = [[pessoa(1, 0.0), mala(9, 0.3)]] * 2
    quadros += [[pessoa(1, 0.0), pessoa(7, 1.5), mala(9, 0.3)]] * 8

    resultado = run_session(cena(*quadros), PLANO, config)

    assert resultado.flags.for_person(7) != []
    assert resultado.queue == []


def test_fila_ranqueia_quem_acumulou_mais_flags():
    """Com um flag por pessoa a fila nunca ordenou nada de verdade."""
    config = Config(pipeline=PipelineConfig(merge_every_frames=1, proximity_flag_s=2.0))
    quadros = [[pessoa(1, 0.0), mala(9, 0.3)]] * 2
    # 7 se aproxima e depois encosta; 8 so passa perto.
    quadros += [[pessoa(1, 0.0), pessoa(7, 1.5), pessoa(8, 1.5), mala(9, 0.3)]] * 6
    quadros += [[pessoa(1, 0.0), pessoa(7, 0.3), pessoa(8, 1.5), mala(9, 0.3)]] * 3

    resultado = run_session(cena(*quadros), PLANO, config)

    assert len(resultado.flags.for_person(7)) == 2
    assert [item.person for item in resultado.queue] == [7]
    assert resultado.queue[0].top_level is FlagLevel.N2


# --- custódia -----------------------------------------------------------------


def test_ausencia_curta_nao_conta_como_retirada():
    """Bagagem some por um frame e volta: oclusão, não furto."""
    quadros = [[pessoa(1, 0.0), mala(9, 0.5)]] * 3 + [[pessoa(1, 0.0)]] * 2
    quadros += [[pessoa(1, 0.0), mala(9, 0.5)]] * 3

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER) == []
    assert resultado.events.of_kind(EventKind.BAG_REMOVED_BY_OWNER) == []


def test_retirada_e_resolvida_uma_vez_so():
    quadros = [[pessoa(1, 0.0), mala(9, 0.5)]] * 3 + [[pessoa(7, 0.5)]] * 30

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER)) == 1


def test_bagagem_longe_de_todos_fica_orfa():
    resultado = run_session(cena([pessoa(1, 0.0)], [pessoa(1, 0.0), mala(9, 50.0)]), PLANO, RAPIDO)

    assert resultado.events.of_kind(EventKind.BAG_OWNED) == []


def test_resultado_reporta_frames_e_duracao():
    resultado = run_session(cena([pessoa(1, 0.0)], [pessoa(1, 1.0)], [pessoa(1, 2.0)]), PLANO)

    assert resultado.frames == 3
    assert resultado.duration_s == 2.0


# --- posse é o depósito (#35) -------------------------------------------------


def mochila(track_id: int, x: float) -> TrackedDetection:
    return TrackedDetection(track_id=track_id, cls="backpack", bbox=(x - 0.2, 0.0, x + 0.2, 0.4))


def test_bagagem_em_transito_nao_entra_no_registro():
    """O registro é de âncoras, e bagagem que nunca parou nunca foi âncora.

    Medido no primeiro vídeo real: o dono foi acusado de furtar a própria mala
    aos 2s e terminou em 1º na fila. A mala nasceu órfã -- ninguém estava
    detectado no único quadro em que ela se registrou -- e bagagem órfã que se
    move resolvia como retirada por estranho. Todo viajante que entra em
    quadro puxando mala é esse caso.
    """
    quadros = [[pessoa(1, 2.0 * i), mala(9, 2.0 * i + 0.3)] for i in range(6)]

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert resultado.events.of_kind(EventKind.BAG_APPEARED) == []
    assert resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER) == []
    assert [item for item in resultado.queue if item.top_level is FlagLevel.N3] == []


def test_mochila_nas_costas_de_quem_anda_nao_vira_bagagem():
    """Mesma regra, e é o ruído dominante em material real: 20 registros de
    bagagem para cerca de 3 malas de verdade, porque toda mochila e bolsa em
    movimento abria uma âncora."""
    quadros = [[pessoa(1, 2.0 * i), mochila(9, 2.0 * i)] for i in range(6)]

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert resultado.events.of_kind(EventKind.BAG_APPEARED) == []


def test_bagagem_parada_entra_no_registro_com_quem_a_depositou():
    """O caminho bom: a mala para, vira âncora, e a posse é de quem estava
    junto no depósito."""
    quadros = [[pessoa(1, 0.5), mala(9, 0.0)] for _ in range(4)]

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.BAG_APPEARED)) == 1
    assert len(resultado.events.of_kind(EventKind.BAG_OWNED)) == 1


def test_posse_sobrevive_ao_dono_sumir_no_quadro_do_deposito():
    """#35 medido: 5% de falha de detecção de pessoa custavam 40% das posses,
    porque a posse dependia de o dono estar detectado num único quadro. A
    rajada medida no CAVIAR tem 12 quadros, então a chance de ele estar
    invisível naquele instante exato era de ~39%.

    Enquanto a bagagem for órfã a pergunta continua aberta, e a janela é o que
    a mantém aberta sem reabrir o ataque da P1.
    """
    quadros = [[mala(9, 0.0)] for _ in range(3)]
    quadros += [[pessoa(1, 0.5), mala(9, 0.0)] for _ in range(2)]

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.BAG_OWNED)) == 1


def test_estranho_que_chega_depois_da_janela_nao_vira_dono():
    """A retentativa não pode reabrir o ataque que a regra P1 fecha: quem se
    aproxima de uma bagagem sem dono não herda a posse dela por ficar por
    perto. Depois da janela, a bagagem segue órfã e levá-la é retirada."""
    quadros = [[mala(9, 0.0)] for _ in range(14)]
    quadros += [[pessoa(7, 0.5), mala(9, 0.0)] for _ in range(3)]
    quadros += [[pessoa(7, 2.0 * i + 0.3), mala(9, 2.0 * i)] for i in range(1, 4)]

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert resultado.events.of_kind(EventKind.BAG_OWNED) == []
    assert len(resultado.events.of_kind(EventKind.BAG_REMOVED_BY_STRANGER)) == 1


def test_bagagem_que_volta_um_pouco_fora_da_ancora_e_readotada():
    """#29: o raio de readoção era o mesmo número que decide 'a bagagem se
    moveu'. Uma bagagem que reaparecia a 0,6m da âncora não era readotada e se
    registrava como bagagem nova, enquanto a original seguia ocluída rumo ao
    timeout -- duas entradas de registro para uma bagagem física."""
    quadros = [[mala(9, 0.0)] for _ in range(4)]
    quadros += [[] for _ in range(6)]
    quadros += [[mala(10, 0.6)] for _ in range(4)]

    resultado = run_session(cena(*quadros), PLANO, RAPIDO)

    assert len(resultado.events.of_kind(EventKind.BAG_APPEARED)) == 1
    assert len(resultado.events.of_kind(EventKind.TRACK_RELINKED)) == 1
