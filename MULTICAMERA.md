# Plano para câmeras, mapa e acompanhamento multicâmera

Estado em 2026-11-06: **cadastro e replay de predições implementados em CPU**;
mosaico, reprodução de vídeo sincronizada, planta e associação entre câmeras
ainda planejados. Os recortes abaixo não têm números de issues atribuídos no GitHub.

## Ponto de partida verificado

- `scripts/serve.py` recebe uma fonte: arquivo, índice de câmera ou URL. Já existe
  painel web com imagem, fila de revisão, estado da captura e apontamento manual.
- `GroundPlane` projeta pixels no chão em metros. Falta um cadastro que coloque
  todas as câmeras no mesmo referencial espacial e temporal.
- Há replay de predições versionadas e testes sintéticos em CPU. O cadastro novo
  compõe manifests de sequência v1 sem modificá-los; mantém a distinção entre
  oracle, sintético e detector por câmera.
- A recuperação de captura cria outro segmento e outra sessão. IDs, grupos e
  posse não atravessam essa lacuna automaticamente.
- Não foram validados neste clone câmeras físicas, RTSP real, inferência em GPU,
  mosaico, planta integrada ou associação de pessoas entre câmeras.

## O que o produto deve mostrar

Um mosaico permite selecionar a câmera e abrir sua imagem e fila. Uma planta
mostra posição das câmeras, cobertura declarada, observações no chão e última
observação de cada trajetória. Para ambientes internos, uma planta em SVG ou
imagem com escala basta; um mapa geográfico pode ser acrescentado para vários
edifícios. Não é necessário começar com um serviço externo de mapas.

Cobertura geométrica não garante visibilidade: há oclusões, objetos pequenos,
iluminação e falhas de captura. O painel deve distinguir observado agora, última
observação e associação incerta. Uma passagem sem imagem não vira trajetória
observada por interpolação. Uma planta desenhada sem calibração pode localizar
câmeras, mas não sustenta medidas de distância ou posição das pessoas.

O ID de trajetória é temporário e local ao experimento, sem nome de pessoa ou
consulta a banco de identidades. Reconhecimento facial continua fora de escopo.

## Laboratórios públicos encontrados

Fontes primárias consultadas em 2026-11-06. Não houve download de vídeos/pesos,
aceite de termos, solicitação de acesso ou execução dessas aplicações.

| Recurso | O que oferece | Uso proposto e limite |
| --- | --- | --- |
| [NVIDIA PhysicalAI-SmartSpaces](https://huggingface.co/datasets/nvidia/PhysicalAI-SmartSpaces) | Conjuntos sincronizados; nas edições 2025/2026, calibração, `map.png`, posições 3D e IDs entre vistas para as cenas sintéticas anotadas. A ficha declara CC BY 4.0. | Melhor candidato inicial para ligar planta e trajetórias. Selecionar uma cena e poucas câmeras RGB; mapas de profundidade são opcionais e grandes. As capturas reais de teste de 2026 não disponibilizam ground truth. |
| [WILDTRACK, EPFL](https://www.epfl.ch/labs/cvlab/data/data-wildtrack/) | Sete câmeras reais, vistas sobrepostas, sincronização, calibração e anotações. | Segundo passo para geometria e oclusões reais. Distinguir os quadros anotados dos vídeos completos; a página descreve taxas de amostragem diferentes. Termos e disponibilidade dos arquivos ainda precisam ser conferidos antes da aquisição. |
| [MultiviewX, autores](https://github.com/hou-yz/MultiviewX) | Seis vistas sintéticas sobrepostas, sincronizadas e calibradas. | Alternativa para geometria. Foi publicado para detecção multivista; não assumir um protocolo completo de tracking temporal ou de custódia. Download e condições de uso não foram exercitados. |
| [MTMMC, autores](https://sites.google.com/view/mtmmc) | Câmeras em campus e fábrica, topologia com áreas internas/externas e vários andares. | Experimento posterior com topologia mais realista. Acesso depende de solicitação e acordo assinado; não é a primeira dependência do desenvolvimento. |

A NVIDIA também publica uma [aplicação de referência MTMC](https://docs.nvidia.com/mms/text/MDX_Multi_Camera_Tracking_App.html)
que simula câmeras por arquivos transmitidos em RTSP. Sua
[interface](https://docs.nvidia.com/mms/text/MDX_Multi_Camera_Tracking_UI.html)
combina vídeos, trajetórias e planta. Serve como referência de interação e
arquitetura; a documentação a apresenta como interface de referência, com
endurecimento para produção ainda a cargo de quem a utiliza. O modo playback
documentado dispensa percepção pesada, mas também não implanta a UI.
Compatibilidade e consumo dessa stack neste Windows não foram verificados.

Esses recursos permitem desenvolver uma rede virtual de câmeras relacionadas.
Vídeos independentes de locais diferentes, ou recortes de uma mesma imagem,
não demonstram associação multicâmera real. Os conjuntos de pessoas acima
também não fornecem, por si, anotações de dono da bagagem ou de furto.

## Entregas pequenas, em ordem

### 1. Cadastro do ambiente e relógio de replay

Implementado em `camera_registry.py`, `multicamera_replay.py` e
`scripts/replay_multicamera.py`: contrato versionado para ambiente, câmeras e
referencial comum, sem integração de detector ou migração da custódia.

- Registrar IDs de ambiente, andar e câmera, fonte local, calibração/referencial,
  posição e cobertura quando conhecidas. Ausência de calibração deve ser explícita.
- Especificar unidade, origem e orientação dos eixos; manter separado o sistema
  de coordenadas em metros da transformação para pixels da planta.
- Registrar offset de cada gravação para uma linha do tempo comum e a precisão
  conhecida dessa sincronização. FPS igual não comprova sincronização.
- Preservar os manifests de sequência existentes; definir composição ou uma
  versão nova, com migração explícita, antes de adicionar campos ao leitor estrito.
- Aceite: IDs duplicados, referências inexistentes e números não finitos são
  recusados. Um fixture sintético com duas câmeras e offsets distintos reproduz
  os mesmos instantes globais sem depender da ordem de leitura, em CPU.

#### Contrato do cadastro v1

Exemplo completo: `tests/fixtures/multicamera/two-cameras/registry.json`.
Todos os campos são obrigatórios, inclusive os que admitem `null`; campos
desconhecidos e chaves JSON duplicadas são recusados.

| Campo | Significado |
| --- | --- |
| `schema_version`, `site_id` | Versão inteira `1` e identificador do ambiente. |
| `floors[]` | Andares com `floor_id` único e `coordinate_frame`. |
| `coordinate_frame` | `frame_id` único no ambiente, `unit: "m"` e descrições não vazias de `origin`, `x_axis`, `y_axis`. Coordenadas 2D cartesianas no chão; andares não compartilham automaticamente um plano. |
| `cameras[]` | IDs únicos `camera_id`, referência `floor_id` e um `segment_id` por câmera nesta versão. |
| `position_m`, `coverage_m` | Par `[x,y]` para a câmera e lista de vértices da cobertura declarada no referencial do andar, ou `null`. Recusa polígonos de área nula; não certifica ausência de oclusões nem toda a topologia do polígono. |
| `projection_frame_id` | ID do referencial do andar se a saída do `GroundPlane` da gravação já estiver nele; `null` quando essa relação é desconhecida. É uma declaração, sem estimativa automática de transformação entre referenciais. |
| `recording` | `path` relativo ao cadastro, `size_bytes` e `sha256` do manifesto de sequência v1. O leitor também valida os hashes internos da sequência. O caminho da fonte local e sua disponibilidade continuam definidos pelo manifesto original. |
| `clock` | `offset_s` finito, `uncertainty_s` não negativo ou `null`, e `method` descrevendo como a sincronização foi estabelecida. |

O tempo nominal é **`t = media_t + offset_s`**, em segundos relativos ao
experimento; pode ser negativo. Não subtrai `start_s` do clip. Quando a incerteza
é conhecida, o intervalo declarado é `[t - uncertainty_s, t + uncertainty_s]`;
`null` indica precisão desconhecida, nunca precisão perfeita. A versão atual
modela offset constante, sem correção de drift ou estimativa de sincronização.
Recusa overflow e offsets que apaguem a ordem dos timestamps por perda de precisão.

`iter_synchronized` intercala os quadros por tempo nominal e desempata por ID de
câmera e índice do quadro, independentemente da ordem de cadastro. Sua janela
opcional é inclusiva no tempo nominal. Não agrupa instantes próximos, não repete
a última observação e não inventa linhas para quadros ausentes. Quadros vazios
e linhas explicitamente `missing` mantêm seus estados originais.

A CLI exporta contexto em `summary.json` e quadros em `frames.jsonl`, em um
diretório novo. Preserva proveniência por câmera e nomes qualificados por
`(camera_id, segment_id, local_track_id)`. As posições projetadas ficam `null`
sem referencial comum declarado. Não exporta assinaturas de aparência, IDs
globais, eventos de custódia ou métricas de tracking. A projeção é checada antes
de criar o diretório final; falhas de escrita posteriores podem deixar saída
parcial, e a CLI termina com erro.

O fixture contém somente números/observações geradas e duas calibrações
sintéticas, sem arquivos de vídeo. A coincidência de posições entre vistas
foi construída; não demonstra associação de identidade ou desempenho de detector.

### 2. Mosaico e planta do laboratório virtual

- Acrescentar reprodução sincronizada com pausa e busca conjunta; começar pelos
  dados sintéticos do contrato, depois adaptar um recorte público autorizado.
- Mostrar estado e idade da imagem por câmera, instante do replay e cobertura
  conhecida. Exibir tracks locais com namespace, sem uni-los visualmente.
- Aceite: câmera atrasada, finalizada ou interrompida mantém indicação própria;
  uma imagem antiga não recebe marcador de tempo atual. Testar câmera ausente,
  lacuna, calibração ausente e IDs locais iguais em câmeras diferentes.
- Identificar em tela se posições vêm de oracle, fixture ou detector. Uma
  demonstração com posições anotadas valida a interface, não a percepção.

### 3. Associação entre vistas, medida separadamente

- Qualificar observações por `(camera_id, segment_id, local_track_id)`. Criar
  hipóteses globais em camada separada, com origem, intervalo e evidências.
- Começar por vistas sobrepostas: posição comum, simultaneidade e consistência
  de movimento. Testar depois passagens sem sobreposição, usando topologia,
  tempos plausíveis e aparência temporária; admitir que a associação falhe.
- Recusar associações incompatíveis e manter alternativas/abstenção quando a
  evidência não resolver. Mesmo número de track nunca comprova mesma pessoa.
- Isolar o estado dos backends antes de executar detectores simultâneos: o
  código instalado de Ultralytics 8.4.126 usa contador global de IDs no tracker.
  A recriação sequencial atual não estabelece segurança concorrente.
- Aceite: casos de troca de IDs, pessoas parecidas, oclusão, relógio desalinhado
  e reconexão, além das passagens corretas. Medir falsas uniões, fragmentações,
  precisão/recall das passagens e erro de localização; IDF1/HOTA quando o
  protocolo e as anotações forem compatíveis. Relatar abstenções.

### 4. Custódia entre vistas e demonstração controlada

- Só então estudar transporte de evidência de posse entre câmeras, preservando
  P1–P4. Um ID global associado não concede posse automaticamente.
- Manter bagagens paradas como âncoras no chão e tratar hipóteses ambíguas sem
  fabricar eventos. Preservar o encerramento de sessão nas lacunas atuais.
- Criar anotações próprias de pessoas, bagagens, posse e passagens legítimas e
  ilegítimas; os benchmarks de pedestres não substituem essa avaliação.
- Aceite: medir falsas transferências de posse e falsos alertas por hora, eventos
  perdidos, latência e carga de revisão, com protocolo e resultados versionados.
  Cada comparação usa mesmo split, seed e métrica, informando quantos runs.
- Validar depois uma câmera e duas vistas em demonstração autorizada com
  voluntários. Isso valida o cenário ensaiado; não demonstra redução de furtos.

## Prioridade de pesquisa

Recomendação: construir o laboratório virtual e reproduzir o baseline real da
percepção como frentes incrementais. Não condicionar o painel à compra de câmeras,
nem tratar um mapa funcional como evidência de que bagagens estão sendo vistas.
A #56 ainda precisa de vídeos e predições reais reproduzíveis neste clone.

Tracking multicâmera e mapas já têm trabalhos e aplicações públicas. A hipótese
de contribuição do custody-watch é medir se a associação conservadora entre vistas
melhora a revisão de mudanças de custódia, sem aumentar falsas acusações. Isso
exige comparação com câmera única, falhas de percepção e passagens legítimas;
não há resultado multicâmera do projeto que sustente essa hipótese ainda.
