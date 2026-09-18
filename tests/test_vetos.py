"""O que a imagem diz sobre uma bagagem antes de ela poder virar âncora.

Três perguntas que só têm resposta em pixel, e por isso são feitas antes da
projeção para o chão. Nenhuma delas é distância — distância segue em metros.
Todas saíram de vídeo real: MEVA e o clipe gerado de portão de embarque.
"""

from custody_watch.tracking import TrackedDetection, anchor_vetoes

PESSOA_EM_PE = (100.0, 100.0, 180.0, 400.0)


def bolsa(bbox, track_id=9, cls="handbag", no_limite=False) -> TrackedDetection:
    return TrackedDetection(track_id, cls, bbox, touches_bottom=no_limite)


def pessoa(bbox=PESSOA_EM_PE, track_id=1) -> TrackedDetection:
    return TrackedDetection(track_id, "person", bbox)


def test_bagagem_pequena_demais_nao_vira_ancora():
    """Abaixo da altura em que o detector é confiável, a posição dela no chão
    é ruído. Medido no vídeo de portão: as duas acusações falsas envolviam
    malas de 27 e 29px, e um piso de 30 a 60px as eliminava sem tocar no
    furto de verdade, de 167px."""
    vetos = anchor_vetoes([bolsa((10, 10, 30, 38))], min_bag_height_px=40.0)

    assert vetos == {9: "pequena"}


def test_bagagem_do_tamanho_certo_passa():
    assert anchor_vetoes([bolsa((10, 10, 60, 90))], min_bag_height_px=40.0) == {}


def test_piso_zero_desliga_a_regra():
    """Caixa anotada à mão é percepção perfeita por construção: o CAVIAR tem
    bagagem de 9 a 22px, e o piso ali só apagaria a avaliação."""
    assert anchor_vetoes([bolsa((10, 10, 30, 20))], min_bag_height_px=0.0) == {}


def test_bagagem_cortada_pela_borda_nao_vira_ancora():
    """O pé da caixa é onde a bagagem toca o chão. Cortada pela borda de
    baixo, o pé vira a borda da imagem: a projeção dá sempre a mesma linha e
    a bagagem parece parada enquanto é puxada. Medido no MEVA: foi assim que
    o ladrão virou dono da bolsa que levava."""
    vetos = anchor_vetoes([bolsa((600, 900, 1000, 1079), no_limite=True)], 40.0)

    assert vetos == {9: "cortada"}


def test_mochila_nas_costas_de_quem_esta_parado_nao_vira_ancora():
    """Parar não é depositar. A mochila de quem espera parado na mesa de
    lanches fica imóvel junto com a pessoa, passa no teste de repouso, e
    quando ela sai andando vira retirada. Medido no MEVA: 3 acusações falsas
    em 25 minutos, todas deste jeito."""
    mochila = bolsa((115, 160, 165, 260), cls="backpack")

    assert anchor_vetoes([pessoa(), mochila], 40.0) == {9: "carregada"}


def test_mala_no_chao_ao_lado_dos_pes_passa():
    """O caso bom, e o que o veto não pode apagar: bagagem apoiada no chão,
    com a base na altura dos pés de quem está do lado."""
    mala = bolsa((150, 300, 230, 398), cls="suitcase")

    assert anchor_vetoes([pessoa(), mala], 40.0) == {}


def test_bolsa_no_banco_ao_lado_de_quem_senta_passa():
    """Os furtos do MEVA eram bolsas em cima de banco, ao lado de alguém
    sentado. Fora do chão, sim — mas não dentro da silhueta de ninguém. Vetar
    isto seria vetar o próprio furto."""
    sentado = pessoa((100.0, 200.0, 200.0, 400.0))
    no_banco = bolsa((170, 290, 260, 340))

    assert anchor_vetoes([sentado, no_banco], 40.0) == {}
