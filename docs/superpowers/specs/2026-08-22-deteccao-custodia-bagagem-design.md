# Detecção de custódia de bagagem — design do v1

**Data:** 2026-08-22
**Status:** aprovado, pendente de plano de implementação
**Projeto:** custody-watch

---

## 1. Problema

Furto de bagagem em aeroportos, durante o tempo de espera do passageiro.

O enquadramento intuitivo — "reconhecimento facial + detecção de comportamento suspeito" — está errado, e o erro é estrutural, não de calibração.

Furto de bagagem não é um problema de **identidade**. É um problema de **custódia de objeto**. A pergunta que importa não é "quem é essa pessoa", e sim:

> A pessoa que saiu com a bagagem é a mesma que a colocou ali?

Essa distinção define o projeto inteiro:

| | "Detectar comportamento suspeito" | "Detectar mudança de custódia" |
|---|---|---|
| Definição do evento | Vaga, subjetiva | Física, verificável |
| Exige identidade? | Sim | Não |
| Rótulo de dataset | Inconsistente por natureza | "bagagem X saiu com pessoa Y ∉ grupo dono" |
| Risco LGPD | Alto (dado sensível, art. 11) | Baixo (sem biometria persistente) |
| Modo de falha | Acusa por aparência | Operador descarta em 10s |

Sistemas de detecção genérica de "comportamento suspeito" não funcionam e reproduzem viés. Detecção de custódia é um problema de engenharia clássico e tratável.

### 1.1 A restrição que domina o design

Prevalência estimada em aeroporto de grande porte (~120k passageiros/dia, ~5 eventos de custódia por passageiro ⇒ ~600k eventos/dia; ~20 furtos/dia):

**Base rate ≈ 1 em 30.000.**

| Recall | FPR | Verdadeiros/dia | Falsos/dia | Precisão |
|---|---|---|---|---|
| 90% | 1% | 18 | 6.000 | 0,3% |
| 90% | 0,1% | 18 | 600 | 2,9% |
| 90% | 0,01% | 18 | 60 | 23% |

Conclusão: **um detector binário de furto é inviável.** Nenhum limiar produz precisão utilizável.

Mas ~600 alertas/dia são ~25/hora, e um operador revisa um clipe de 15s em ~30s. Ou seja, o volume é absorvível **se o sistema for de triagem, não de acusação**.

### 1.2 Objetivo real do produto

> Reduzir ~600 mil eventos de custódia por dia a algumas centenas de clipes ranqueados que valem o tempo de um operador humano.

O sistema é **apoio a um profissional que já monitora as câmeras**. Nunca decide sozinho, nunca acusa, nunca identifica.

---

## 2. Escopo do v1

**Dentro:**
- Vídeo gravado, **uma câmera**, processamento offline
- Saída: fila ranqueada de clipes, cada um com score e explicação em português
- Validação no PETS2007 (tem cenário de furto de bagagem rotulado)

**Fora:**
- Tempo real
- Multi-câmera / MTMC
- Fine-tuning de detector
- Integração com VMS
- Banco de dados persistente
- **Reconhecimento facial — fora permanentemente, não só do v1**

### 2.1 Por que câmera única

A transferência de custódia acontece **dentro de uma única câmera**. Multi-câmera agrega duas coisas — formar grupos mais cedo (viu as pessoas chegarem juntas) e seguir o suspeito depois do evento. Ambas valiosas, nenhuma essencial para detectar o evento.

MTMC é o item mais caro do roadmap e o único capaz de consumir o projeto inteiro sozinho. Fica para o v2, usando as 4 câmeras com sobreposição do PETS2007.

---

## 3. Princípios de design

Três regras carregam o sistema. Toda decisão de implementação deve ser checada contra elas.

### P1 — Posse só flui por vínculo forte

Se pertencer a um grupo dependesse apenas de proximidade e tempo, um ladrão sentaria ao lado da vítima por três minutos e o sistema o declararia dono da bagagem. Ele não burlaria o sistema; usaria o sistema como projetado. E o comportamento exigido (sentar perto e esperar) é exatamente o comportamento natural do furto.

Proximidade prolongada com bagagem alheia deve **aumentar** a suspeição, não zerá-la.

### P2 — Bagagem parada é âncora espacial, não problema de re-ID

Duas malas pretas idênticas lado a lado, alguém passa na frente, o tracker reassocia trocado. O mapa de posse corrompe e o sistema gera um furto que não existiu. Aparência idêntica é justamente o que impede o tracker de se autocorrigir.

Bagagem parada não teleporta. **A posição é a identidade.** Reassociação por proximidade da última posição conhecida, não por aparência.

### P3 — Incerteza suprime alerta, nunca gera

Existe um estado `AMBIGUA` de primeira classe. Quando a reassociação é incerta, o sistema marca e **cala**, em vez de chutar.

É a diferença entre um sistema que o operador confia e um que ele desliga na segunda semana.

### P4 — Flags são relacionais, nunca atributivos

> Um flag descreve a relação entre uma pessoa e uma bagagem ao longo do tempo. Nunca um atributo estático da pessoa.

"Não carrega bagagem" é atributo: dispara em 20-30% da população de um aeroporto (funcionários de limpeza, tripulação, staff de lojas, quem foi buscar parente, quem já despachou). Um flag que dispara em um quarto da população não carrega informação — só dilui os flags que importam.

E marca sistematicamente trabalhadores do aeroporto, todo dia. É o problema da "vadiagem" da licitação original do Smart Sampa reaparecendo com outra roupa.

"Se aproximou de bagagem de três grupos distintos em oito minutos" é relacional: raro, informativo, explicável e defensável.

---

## 4. Arquitetura

```
vídeo
  │
  ├─► Detector ─────────► pessoas + bagagens por frame
  │     interface trocável; adapter YOLO26 como default
  │
  ├─► GroundPlane ──────► homografia: pixels → metros
  │
  ├─► PersonTracker ────► track_ids estáveis (ByteTrack/BoT-SORT + OSNet)
  │
  ├─► BagRegistry ──────► âncoras espaciais (ver P2)
  │
  ├─► PartyManager ─────► grupos com vínculo forte/fraco
  │
  ├─► CustodyFSM ───────► estado por bagagem
  │
  ├─► FlagEngine ───────► flags relacionais com decaimento
  │
  └─► AlertQueue ───────► ranqueia, recorta clipe, gera frase
```

Cada módulo tem uma responsabilidade e uma interface. `PartyManager` não sabe o que é uma câmera; `CustodyFSM` não sabe o que é um detector.

### 4.1 Detector

Interface `Detector` com adapters trocáveis. Default: **YOLO26** (Ultralytics, jan/2026).

Justificativa da escolha, mapeada ao problema:

| Característica do YOLO26 | Relevância aqui |
|---|---|
| NMS-free (end-to-end) | Principal ganho. NMS suprime caixas sobrepostas, e saguão de aeroporto é pessoas e malas sobrepostas |
| STAL + Progressive Loss | Bagagem a 15m em grande-angular é objeto pequeno |
| DFL removido, +43% CPU | Pouco relevante com GPU; importa se for para edge |
| MuSGD | Só na fase de fine-tuning com dados encenados |

Classes COCO usadas: `person`, `backpack`, `handbag`, `suitcase`. Sem fine-tuning no v1.

**Risco a medir, não assumir:** o mecanismo central do ByteTrack é a associação em dois estágios usando detecções de alta *e* baixa confiança. Modelo NMS-free faz atribuição um-pra-um e tende a produzir distribuição de confiança mais concentrada, podendo sobrar menos material para o segundo passe. A interface trocável existe justamente para que essa medição não custe caro.

### 4.2 GroundPlane

Todos os limiares do sistema são em **metros**. Distância em pixels varia com profundidade — 50px no fundo da cena são ~4m, na frente são ~40cm. Sem correção, o limiar de "3 metros" é ficção.

Homografia para o plano do chão. O PETS2007 **já distribui arquivos de calibração de câmera**, então no v1 sai de graça. Para vídeo gravado pelo usuário, calibrar com objeto de dimensão conhecida no chão.

### 4.3 PartyManager

```
party = { members: {track_id: STRONG | WEAK}, bags: [bag_id] }
```

| Evento | Vínculo | Transfere posse? |
|---|---|---|
| Entraram na cena juntos (<2m, co-movimento ≥3s) | STRONG | sim |
| Co-movimento sustentado (≥5m de deslocamento junto, <2m de distância) | STRONG | sim |
| Já trocaram bagagem entre si | STRONG | sim |
| Apenas proximidade estática (>60s parados perto) | WEAK | **não** — apenas atenua o flag |

**A assimetria entre as duas primeiras linhas é intencional.** Formação na entrada da cena aceita evidência mais fraca (3s de co-movimento) porque é o momento natural em que grupos se formam e o custo de simular é alto — exigiria o ladrão já estar acompanhando a vítima antes. Entrada tardia exige muito mais (5m de deslocamento conjunto) porque é o caminho explorável: é ali que o atacante tentaria se inserir. Quanto mais tarde o vínculo se forma, mais caro ele deve ser.

**Armadilha:** co-movimento exige **deslocamento real**. Duas pessoas paradas têm vetor de velocidade zero, e zero correlaciona perfeitamente com zero. Sem exigir deslocamento mínimo, todos os sentados na praça de alimentação viram uma party única — e o exploit de P1 volta pela porta dos fundos.

**Reentrada após perda de ID:** re-ID de aparência (embedding) + plausibilidade espaço-temporal (voltou por entrada coerente, em janela de tempo coerente). Proximidade entra como desempate, nunca como evidência principal. Sem confiança suficiente ⇒ P3.

### 4.4 CustodyFSM

```
NOVA ──back-tracing: quem trouxe?──┬──► POSSUIDA(party) ──► ACOMPANHADA
                                   │         ▲                   │
                                   │         └── dono volta ─────┤ dono >3m
                                   │                             ▼   por >25s
                                   └──► ORFA                DESACOMPANHADA
                                                                 │
                    oclusão ambígua ──► AMBIGUA (alertas OFF)    │ bagagem move
                                                                 ▼
                                              ┌──────────────────┴─────┐
                                        carregador ∈ party?      carregador ∉ party?
                                              │                        │
                                              ▼                        ▼
                                     RETIRADA_DONO (ok)        RETIRADA_ESTRANHO (N3)
```

Limiares default `3m` / `25s` vêm do protocolo do PETS2007, tornando o ground truth comparável ao benchmark sem trabalho extra.

**Vinculação inicial** por *back-tracing*: quando uma bagagem é detectada parada pela primeira vez, o sistema olha para trás no buffer e identifica quem a depositou. Técnica padrão da literatura de abandoned object detection.

### 4.5 FlagEngine

```
flag  = (tipo, pessoa, bagagem, t, peso)
score(pessoa, t) = Σ peso_i · exp( -(t - t_i) / τ )      τ ≈ 15 min
```

Decaimento exponencial impede que tempo de permanência vire suspeição — sem ele, quem passou 4h no aeroporto acumula score por existir.

Níveis:

| Nível | Gatilho | Ação |
|---|---|---|
| **Contexto** | bagagem com grupo conhecido / órfã / custódia indeterminada | Estado interno, não pontua |
| **N1 fraco** | Permanência **>90s** a <2m de bagagem de outro grupo · Aproximação e afastamento sem contato, **≥2 vezes** · Bagagem desacompanhada >25s | Acumula, não entra na fila |
| **N2 relacional** | Contato físico com bagagem de outro grupo · Proximidade a ≥3 grupos distintos **em janela de 10 min** · Vínculo fraco recém-formado seguido de contato | Entra na fila |
| **N3 custódia** | Bagagem removida por pessoa fora do grupo dono · Bagagem órfã removida · Remoção seguida de mudança brusca de direção/velocidade | Topo da fila |

**Funcionários** geram N2/N3 legitimamente (limpeza manuseia bagagem). Mitigação preferida: lista de zonas/horários onde manuseio por staff é esperado. Evita depender de classificação de uniforme e não introduz nenhum tratamento biométrico.

### 4.6 AlertQueue

Ordena por score. Cada item carrega clipe de ±10s e uma frase gerada por template.

**Explicação é requisito, não enfeite.** O operador precisa ver *por quê*, não um número. Sem isso o sistema é uma caixa-preta que não sustenta revisão nem contestação.

---

## 5. Avaliação

Não é mAP.

| Métrica | Definição |
|---|---|
| **P_miss @ RFA** | Probabilidade de perder o evento a uma dada taxa de falsos alarmes por minuto. Métrica padrão do NIST ActEV para esta família de problema |
| Falsos alarmes / min de vídeo | Custo operacional direto |
| Posição do evento verdadeiro na fila | Mede a qualidade do ranqueamento, que é o que o produto realmente entrega |

Ground truth: cenários rotulados do PETS2007.

---

## 6. Dados

Progressão em três etapas, do mais barato ao mais caro:

1. **PETS2007** — cenários de *loitering*, remoção de bagagem acompanhada (furto) e bagagem desacompanhada, 4 câmeras, com calibração. Poucos eventos, resolução antiga, mas positivos reais e ground truth pronto. Começa hoje.
2. **Encenação própria** — quando o v1 provar o conceito e for preciso treinar o classificador de evento (v2). Figurantes, câmera fixa, ground truth controlado. Única fonte de positivos sob medida.
3. **Sintético** (Unity Perception / Omniverse Replicator) — apenas se faltar variação de ângulo e iluminação. Não como fonte única, por causa do gap sim-to-real.

Complementares para pré-treino: PETS2006, ABODA, CAVIAR, UCF-Crime, MEVA/VIRAT.

**Não confundir:** SIXray, OPIXray, PIDray, GDXray, HiXray, CLCXray são datasets de **raio-X de inspeção de bagagem** (achar arma dentro da mala). Problema completamente diferente.

---

## 7. Modos de falha conhecidos

| Falha | Tratamento |
|---|---|
| Casais, famílias, grupos compartilhando bagagem | PartyManager (§4.3) — principal motivação do design |
| Ladrão "entrando" num grupo por proximidade | P1 — vínculo forte vs fraco |
| Funcionários manuseando bagagem legitimamente | Zonas/horários de manuseio esperado (§4.5) |
| "Fica de olho na minha mala" | Party com vínculo forte cobre o caso |
| Malas idênticas, troca de ID em oclusão | P2 + P3 |
| Pixels por metro insuficientes | Verificar PPM da câmera antes de instalar; limitação física, não de software |
| Permanência longa derrota background de curto prazo | Modelo de background duplo (curto/longo prazo) |
| Contraluz de janelões | Conhecido, sem mitigação boa no v1 |

---

## 8. Stack e decisões

| Decisão | Escolha | Motivo |
|---|---|---|
| Detector | YOLO26 atrás de interface trocável | §4.1 |
| Tracker | ByteTrack / BoT-SORT + OSNet | Maduro, integrado |
| Licença | **AGPL-3.0** | Ultralytics é AGPL e o copyleft é viral inclusive sobre uso em rede. Fecha a porta comercial sem Enterprise License; a interface trocável mantém a saída barata (RF-DETR / D-FINE são Apache-2.0) |
| Toolchain | `uv` | Já instalado; gerencia Python e dependências |
| Python | 3.11+ | |

Hardware de desenvolvimento: RTX 5060 Ti 16GB — folgado para o v1 e para fine-tuning futuro.

---

## 9. Sequência de construção

Cada etapa é testável isoladamente contra o PETS2007.

1. `GroundPlane` — homografia e conversão para metros
2. `Detector` + `PersonTracker` — pipeline de percepção
3. `BagRegistry` — âncoras espaciais, estado `AMBIGUA`
4. `PartyManager` — vínculo forte/fraco
5. `CustodyFSM` — máquina de estados
6. `FlagEngine` — flags e decaimento
7. `AlertQueue` — ranqueamento, clipe, explicação
8. Harness de avaliação — P_miss @ RFA

---

## 10. Contexto legal (informativo para o v1, vinculante depois)

Aeroporto brasileiro é operado por concessionária **privada**. Diferente do caso Smart Sampa, **a LGPD se aplica integralmente** — não cabe a exceção de segurança pública do art. 4º, III.

- Detecção de custódia **sem identidade**: legítimo interesse (art. 7º, IX) é sustentável
- Reconhecimento facial contra banco de dados: dado pessoal sensível (art. 5º, II), exige art. 11 — inviável para passageiro comum
- **RIPD obrigatório** (art. 38) em qualquer cenário de produção
- PL 2338/2023 (aprovado no Senado em dez/2024, em tramitação na Câmara) classifica identificação biométrica remota como risco excessivo, com exceções que **não** alcançam operador privado de aeroporto

A arquitetura sem identidade não é apenas mais defensável eticamente — é a única comercialmente viável.

Embeddings de aparência usados na re-ID são **efêmeros**, com escopo de sessão, descartados ao fim do processamento. Não constituem base biométrica.

---

## 11. Fora de escopo, permanentemente

- Reconhecimento facial
- Identificação de pessoas contra qualquer banco de dados
- Flags baseados em atributos estáticos de pessoas (P4)
- Decisão automatizada sem revisão humana
