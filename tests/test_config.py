import json
from dataclasses import FrozenInstanceError

import pytest

from custody_watch import alerts, bag_registry, custody, flags, party
from custody_watch.config import (
    SAFE_BOUNDS,
    Config,
    UnsafeValueError,
    load_config,
)


def test_defaults_batem_com_as_constantes_dos_modulos():
    """Trava a igualdade entre config e constantes.

    Sem este teste os dois lugares divergem em silêncio, e o sistema passa a
    se comportar diferente conforme quem chamou passou config ou não.
    """
    default = Config()

    assert default.party.proximity_m == party.PROXIMITY_M
    assert default.party.late_join_extent_m == party.LATE_JOIN_EXTENT_M
    assert default.party.min_extent_m == party.MIN_EXTENT_M
    assert default.party.min_overlap_samples == party.MIN_OVERLAP_SAMPLES
    assert default.party.min_overlap_s == party.MIN_OVERLAP_S
    assert default.party.max_gap_s == party.MAX_GAP_S
    assert default.party.time_tolerance_s == party.TIME_TOLERANCE_S

    assert default.custody.unattended_distance_m == custody.UNATTENDED_DISTANCE_M
    assert default.custody.unattended_time_s == custody.UNATTENDED_TIME_S

    assert default.registry.moved_threshold_m == bag_registry.MOVED_THRESHOLD_M
    assert default.registry.ambiguity_radius_m == bag_registry.AMBIGUITY_RADIUS_M

    assert default.flags.tau_s == flags.TAU_S
    assert default.alerts.clip_margin_s == alerts.CLIP_MARGIN_S


def test_todo_limiar_perigoso_tem_faixa_declarada():
    """Um limiar sem faixa é um limiar que ninguém pensou em proteger."""
    protegidos = {
        "party.late_join_extent_m",
        "party.min_extent_m",
        "party.proximity_m",
        "party.time_tolerance_s",
        "party.min_overlap_s",
        "party.max_gap_s",
        "custody.unattended_distance_m",
        "custody.unattended_time_s",
        "registry.moved_threshold_m",
        "flags.tau_s",
    }
    assert protegidos <= set(SAFE_BOUNDS)


def test_toda_faixa_tem_razao_escrita():
    for chave, bounds in SAFE_BOUNDS.items():
        assert bounds.reason.strip(), f"{chave} tem faixa sem razão declarada"
        assert bounds.minimum < bounds.maximum


def test_defaults_ficam_dentro_das_proprias_faixas():
    """Se um default viola a própria faixa, um dos dois está errado."""
    default = Config()
    for chave, bounds in SAFE_BOUNDS.items():
        secao, campo = chave.split(".")
        valor = getattr(getattr(default, secao), campo)
        assert bounds.minimum <= valor <= bounds.maximum, f"default de {chave} fora da faixa"


def test_carrega_json_parcial_e_mescla_com_defaults(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"custody": {"unattended_time_s": 30.0}}), encoding="utf-8")

    config = load_config(caminho)

    assert config.custody.unattended_time_s == 30.0
    assert config.custody.unattended_distance_m == Config().custody.unattended_distance_m
    assert config.party.late_join_extent_m == Config().party.late_join_extent_m


def test_valor_fora_da_faixa_e_rejeitado(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"party": {"late_join_extent_m": 0.5}}), encoding="utf-8")

    with pytest.raises(UnsafeValueError) as erro:
        load_config(caminho)

    assert "late_join_extent_m" in str(erro.value)
    assert "0.5" in str(erro.value)


def test_mensagem_de_erro_cita_o_ataque_que_o_limite_previne(tmp_path):
    """Erro de config é documentação. 'Fora da faixa' não ensina nada."""
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"party": {"min_extent_m": 0.0}}), encoding="utf-8")

    with pytest.raises(UnsafeValueError) as erro:
        load_config(caminho)

    assert SAFE_BOUNDS["party.min_extent_m"].reason in str(erro.value)


def test_override_explicito_e_aceito(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text(
        json.dumps(
            {
                "party": {
                    "late_join_extent_m": {
                        "value": 2.0,
                        "unsafe_override": "portão 12 tem campo de só 4m, medido em 2026-09-01",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(caminho)

    assert config.party.late_join_extent_m == 2.0


def test_override_sem_justificativa_e_rejeitado(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text(
        json.dumps({"party": {"late_join_extent_m": {"value": 2.0, "unsafe_override": "  "}}}),
        encoding="utf-8",
    )

    with pytest.raises(UnsafeValueError, match="justificativa"):
        load_config(caminho)


def test_override_exige_justificativa_com_substancia(tmp_path):
    """'ok' não é justificativa. O campo existe para deixar rastro."""
    caminho = tmp_path / "c.json"
    caminho.write_text(
        json.dumps({"party": {"late_join_extent_m": {"value": 2.0, "unsafe_override": "ok"}}}),
        encoding="utf-8",
    )

    with pytest.raises(UnsafeValueError, match="justificativa"):
        load_config(caminho)


def test_override_dentro_da_faixa_nao_precisa_de_justificativa(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"party": {"late_join_extent_m": 6.0}}), encoding="utf-8")

    assert load_config(caminho).party.late_join_extent_m == 6.0


def test_secao_desconhecida_e_rejeitada(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"partyy": {"proximity_m": 2.0}}), encoding="utf-8")

    with pytest.raises(ValueError, match="seção desconhecida"):
        load_config(caminho)


def test_campo_desconhecido_e_rejeitado(tmp_path):
    """Typo silencioso vira config ignorada, que vira comportamento surpresa."""
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"party": {"proximity_metros": 2.0}}), encoding="utf-8")

    with pytest.raises(ValueError, match="campo desconhecido"):
        load_config(caminho)


def test_arquivo_inexistente_falha_alto(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nao_existe.json")


def test_config_de_exemplo_do_repositorio_carrega():
    """`config/default.json` precisa ser válido, senão é documentação mentirosa."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    assert load_config(raiz / "config" / "default.json") == Config()


def test_config_e_imutavel():
    """A exceção certa tem nome.

    `pytest.raises(Exception)` passaria também se a atribuição falhasse por um
    typo no nome do campo — afirmaria imutabilidade sem testá-la.
    """
    config = Config()
    with pytest.raises(FrozenInstanceError):
        config.party.proximity_m = 99.0


def test_max_occlusion_curto_demais_e_recusado(tmp_path):
    """Abaixo de 5s, alguém parado em frente à bagagem apaga a custódia e o
    falso alarme de retirada volta — que é o defeito que este limiar existe
    para fechar."""
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"custody": {"max_occlusion_s": 2.0}}), encoding="utf-8")

    with pytest.raises(UnsafeValueError, match="custody.max_occlusion_s"):
        load_config(caminho)


def test_max_occlusion_longo_demais_e_recusado(tmp_path):
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"custody": {"max_occlusion_s": 600.0}}), encoding="utf-8")

    with pytest.raises(UnsafeValueError, match="custody.max_occlusion_s"):
        load_config(caminho)


def test_erro_de_max_occlusion_nomeia_o_ataque(tmp_path):
    """Mensagem de erro de config é documentação: precisa dizer o que o limite
    previne, não só que o número é inválido."""
    caminho = tmp_path / "c.json"
    caminho.write_text(json.dumps({"custody": {"max_occlusion_s": 600.0}}), encoding="utf-8")

    with pytest.raises(UnsafeValueError) as erro:
        load_config(caminho)

    assert SAFE_BOUNDS["custody.max_occlusion_s"].reason in str(erro.value)


def test_missing_frames_tem_faixa_segura(tmp_path):
    """A 5 quadros esse limiar disparava um N3 falso contra quem passava na
    frente da bagagem. Limiar relevante a ataque precisa de faixa declarada."""
    caminho = tmp_path / "c.json"

    caminho.write_text(
        json.dumps({"pipeline": {"missing_frames_before_occluded": 1}}), encoding="utf-8"
    )
    with pytest.raises(UnsafeValueError, match="missing_frames_before_occluded"):
        load_config(caminho)

    caminho.write_text(
        json.dumps({"pipeline": {"missing_frames_before_occluded": 200}}), encoding="utf-8"
    )
    with pytest.raises(UnsafeValueError, match="missing_frames_before_occluded"):
        load_config(caminho)


def test_nome_antigo_de_missing_frames_falha_alto(tmp_path):
    """Renomear campo é melhor que manter nome que descreve comportamento
    removido. load_config já recusa campo desconhecido, então a renomeação
    não passa em silêncio."""
    caminho = tmp_path / "c.json"
    caminho.write_text(
        json.dumps({"pipeline": {"missing_frames_before_removal": 5}}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="campo desconhecido"):
        load_config(caminho)
