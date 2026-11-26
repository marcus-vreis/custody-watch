"""Agregação sobre seeds: média e IC t de Student com valores conferidos à mão."""

import pytest

from custody_watch.scenario_seed_stats import (
    RunOutcome,
    aggregate_over_seeds,
    ci_description,
    seed_interval,
)


def test_cinco_seeds_ic_t_conferido_a_mao():
    # média 0,8; s = √(0,08/4) = 0,141421; t(0,975; 4) = 2,776445;
    # meia-largura = 2,776445 · 0,141421 / √5 = 2,776445 · 0,0632456 = 0,175598
    intervalo = seed_interval([0.6, 0.8, 0.8, 1.0, 0.8])

    assert intervalo.n_seeds == 5
    assert intervalo.media == pytest.approx(0.8)
    assert intervalo.ic_inf == pytest.approx(0.624402, abs=1e-6)
    assert intervalo.ic_sup == pytest.approx(0.975598, abs=1e-6)


def test_ic_e_limitado_a_zero_e_um():
    # média 0,75; s = 0,25; t(0,975; 2) = 4,302653; meia-largura = 0,621035
    intervalo = seed_interval([0.5, 0.75, 1.0])

    assert intervalo.media == pytest.approx(0.75)
    assert intervalo.ic_inf == pytest.approx(0.128965, abs=1e-6)
    assert intervalo.ic_sup == 1.0


def test_uma_seed_nao_tem_ic():
    intervalo = seed_interval([0.5])

    assert intervalo.n_seeds == 1
    assert intervalo.media == 0.5
    assert intervalo.ic_inf is None
    assert intervalo.ic_sup is None


def test_taxas_iguais_dao_ic_degenerado():
    intervalo = seed_interval([1.0] * 6)

    assert intervalo.media == 1.0
    assert intervalo.ic_inf == 1.0
    assert intervalo.ic_sup == 1.0


@pytest.mark.parametrize("taxas", [[], [1.5], [-0.1, 0.2], [float("nan"), 0.5]])
def test_taxas_invalidas_sao_recusadas(taxas):
    with pytest.raises(ValueError):
        seed_interval(taxas)


def _run(seed, correta, variante="sistema", principio="P1", fps=3.0, ruido="leve"):
    return RunOutcome(variante, principio, fps, ruido, seed, correta)


def test_agrega_por_celula_e_taxa_por_seed():
    # P1 a 3 fps, ruído leve: dois cenários por seed.
    # seed 0: 2/2 = 1,0; seed 1: 1/2 = 0,5; seed 2: 2/2 = 1,0 → média 5/6
    corridas = [
        _run(0, True),
        _run(0, True),
        _run(1, True),
        _run(1, False),
        _run(2, True),
        _run(2, True),
        # outra célula: baseline, mesma seed — não pode contaminar a do sistema
        _run(0, False, variante="baseline"),
    ]

    linhas = aggregate_over_seeds(corridas)

    assert [(r["variante"], r["principio"]) for r in linhas] == [
        ("baseline", "P1"),
        ("sistema", "P1"),
    ]
    sistema = linhas[1]
    assert sistema["seeds"] == [0, 1, 2]
    assert sistema["execucoes_por_seed"] == [2, 2, 2]
    assert sistema["corretas_por_seed"] == [2, 1, 2]
    assert sistema["taxa_por_seed"] == [1.0, 0.5, 1.0]
    assert sistema["n_seeds"] == 3
    assert sistema["media"] == pytest.approx(5 / 6)
    # s = √(1/12) = 0,288675; meia-largura = 4,302653 · 0,288675 / √3 = 0,717109
    assert sistema["ic_inf"] == pytest.approx(5 / 6 - 0.717109, abs=1e-6)
    assert sistema["ic_sup"] == 1.0
    assert linhas[0]["n_seeds"] == 1
    assert linhas[0]["ic_inf"] is None


def test_sem_ruido_vira_uma_seed_sem_ic():
    linhas = aggregate_over_seeds(
        [_run(None, True, ruido="sem_ruido"), _run(None, False, ruido="sem_ruido")]
    )

    assert len(linhas) == 1
    assert linhas[0]["seeds"] == [None]
    assert linhas[0]["media"] == 0.5
    assert linhas[0]["ic_inf"] is None
    assert linhas[0]["ic_sup"] is None


def test_separa_fps_e_nivel_de_ruido():
    corridas = [
        _run(0, True, fps=3.0),
        _run(0, False, fps=25.0),
        _run(0, False, ruido="forte"),
    ]

    chaves = [(r["fps"], r["ruido"], r["media"]) for r in aggregate_over_seeds(corridas)]

    assert chaves == [(3.0, "forte", 0.0), (3.0, "leve", 1.0), (25.0, "leve", 0.0)]


def test_mistura_de_execucao_sem_seed_com_seeds_e_recusada():
    corridas = [_run(None, True), _run(0, True)]

    with pytest.raises(ValueError, match="mistura"):
        aggregate_over_seeds(corridas)


def test_descricao_do_metodo_registra_limites():
    descricao = ci_description()

    assert descricao["nivel"] == 0.95
    assert "t de Student" in descricao["metodo"]
    assert any("sem IC" in limite for limite in descricao["limites"])
    assert any("degenerado" in limite for limite in descricao["limites"])
