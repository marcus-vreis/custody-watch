# Plano para câmeras, mapa e acompanhamento multicâmera

Estado em 2026-11-07: **cadastro, relógio comum, mosaico e planta de replay
implementados em CPU**. Associação entre câmeras e transferência de custódia
continuam planejadas. O roadmap separa implementações de validações ainda pendentes.

- [x] Cadastro de ambiente e relógio comum de replay.
- [x] Mosaico, planta e navegação sincronizada com fixtures e vídeo local gerado.
- [x] Executar recortes públicos NVIDIA e WILDTRACK no importador e no painel, em oracle.
- [x] Recusar posições sem contato comprovado com o chão; projeção diagnóstica separada (#75).
- [ ] Definir evidência de contato por observação e medir sua confiabilidade antes de liberar posições.
- [ ] Medir associação conservadora entre vistas, incluindo recusas e ambiguidades.
- [ ] Medir custódia entre vistas com anotações próprias de posse.
- [ ] Validar câmeras físicas e realizar demonstração controlada autorizada.

O baseline real de percepção da #56 continua em aberto e é necessário para
avaliar custódia. Concluir a interface não conclui essas validações.
Resultados e limites dos recortes: [validação pública](PUBLIC_DATASET_VALIDATION.md).

MTMMC foi retirada do plano ativo por decisão do mantenedor: o projeto é pessoal
e as exigências de vínculo acadêmico do processo de acesso não correspondem ao
seu contexto. O registro histórico da consulta permanece; não há tarefa pendente
de obter afiliação, assinatura de orientador ou solicitar acesso a essa base.

## Ponto de partida verificado

- `scripts/serve.py` recebe uma fonte: arquivo, índice de câmera ou URL. Já existe
  painel web com imagem, fila de revisão, estado da captura e apontamento manual.
- `GroundPlane` projeta pixels no chão em metros. O cadastro multicâmera declara
  a relação com o referencial do andar e o offset temporal de cada gravação.
- Há replay de predições versionadas e testes sintéticos em CPU. O cadastro novo
  compõe manifests de sequência v1 sem modificá-los; mantém a distinção entre
  oracle, sintético e detector por câmera.
- A recuperação de captura cria outro segmento e outra sessão. IDs, grupos e
  posse não atravessam essa lacuna automaticamente.
- Mosaico e planta foram validados com observações sintéticas e decodificação
  de vídeo local gerado para teste. Câmeras físicas, RTSP real, inferência em GPU
  e associação de pessoas entre câmeras continuam sem validação neste clone.

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

### Ajustes da interface ainda em aberto

- [x] Ampliar câmera/zoom para inspecionar caixas pequenas do mosaico (#79).
- Inspecionar um ID com câmera, quadro original, tempo e coordenadas; apresentar
  comparação com gabarito apenas quando esse dado estiver disponível e rotulado.
- Reduzir sobreposição e corte dos rótulos da planta em cenas densas.
- Refinar hierarquia visual e navegação conforme os fluxos de acompanhamento.

O link por câmera/instante já permite abrir casos específicos para revisão.
A política conservadora da #75 recusa todas as posições dos caches atuais para
associação; a planta só mostra essas projeções mediante opção diagnóstica explícita.
Os ajustes de inspeção e um contrato de evidência de contato continuam pendentes.

O botão **Ampliar** pausa o replay e congela o quadro exibido, com câmera,
segmento, índice, tempo da imagem/mídia e idade ao abrir. O modal identifica
oracle/sintético/detector e imagem histórica; não lista tracks de outros quadros
como se estivessem presentes nessa imagem. Zoom de 100% a 400% é relativo ao
ajuste à largura, com rolagem; **Ajustar à largura** retorna ao início da imagem.
Esc ou **Fechar inspeção** encerra e devolve foco ao botão da câmera. O replay
permanece pausado. Sem imagem, o botão fica desabilitado; uma falha ao carregar
é explícita e não substitui silenciosamente o quadro.

A inspeção pede `/api/image` com `resolution=source`: conserva a resolução do
arquivo local e codifica JPEG com qualidade 95; a prévia continua limitada a
1280×720 e qualidade 80. O parâmetro opcional aceita apenas `preview` e `source`;
ausência mantém a prévia. Parâmetros repetidos, vazios e desconhecidos são
recusados. Ambas as variantes validam câmera/segmento/quadro e integridade da
fonte antes/depois da decodificação, liberando o decoder. Esquemas sintéticos
continuam SVGs rotulados. Nenhum zoom recupera detalhes ausentes da fonte.

## Laboratórios públicos encontrados

Fontes primárias consultadas em 2026-11-06. Não houve download de vídeos/pesos,
aceite de termos, solicitação de acesso ou execução dessas aplicações.

| Recurso | O que oferece | Uso proposto e limite |
| --- | --- | --- |
| [NVIDIA PhysicalAI-SmartSpaces](https://huggingface.co/datasets/nvidia/PhysicalAI-SmartSpaces) | Conjuntos sincronizados; nas edições 2025/2026, calibração, `map.png`, posições 3D e IDs entre vistas para as cenas sintéticas anotadas. A ficha declara CC BY 4.0. | Melhor candidato inicial para ligar planta e trajetórias. Selecionar uma cena e poucas câmeras RGB; mapas de profundidade são opcionais e grandes. As capturas reais de teste de 2026 não disponibilizam ground truth. |
| [WILDTRACK, EPFL](https://www.epfl.ch/labs/cvlab/data/data-wildtrack/) | Sete câmeras reais, vistas sobrepostas, sincronização, calibração e anotações. | Segundo passo para geometria e oclusões reais. Distinguir os quadros anotados dos vídeos completos; a página descreve taxas de amostragem diferentes. Termos e disponibilidade dos arquivos ainda precisam ser conferidos antes da aquisição. |
| [MultiviewX, autores](https://github.com/hou-yz/MultiviewX) | Seis vistas sintéticas sobrepostas, sincronizadas e calibradas. | Alternativa para geometria. Foi publicado para detecção multivista; não assumir um protocolo completo de tracking temporal ou de custódia. Download e condições de uso não foram exercitados. |
| [MTMMC, autores](https://sites.google.com/view/mtmmc) | Câmeras em campus e fábrica, topologia com áreas internas/externas e vários andares. | Fora do plano ativo por decisão do mantenedor para o projeto pessoal; consulta de acesso registrada, sem execução. |

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

#### Saída de replay v2: posição incerta (#75)

O cadastro e os manifests de sequência continuam estritamente na versão 1.
Desde a #75, `summary.json`, os quadros de `frames.jsonl` e as respostas de
`/api/config` e `/api/state` declaram `schema_version: 2`. Consumidores da saída
v1 precisam migrar: a posição antes projetada em `position_m` passa para
`projected_position_m` e não comprova contato com o chão.

Cada track contém:

| Campo | Contrato v2 |
| --- | --- |
| `position_m` | `null` para todos os caches atuais: não há evidência por observação que qualifique contato no chão. |
| `projected_position_m` | Projeção bruta da base da bbox em metros, apenas diagnóstica; `null` sem referencial comum. Valores não finitos continuam recusados. |
| `position_status` | `no_common_frame`, `image_border` se `touches_bottom=true`, ou `ground_contact_unknown`. `false`/`null` não comprovam pés visíveis. |
| `association_eligible` | Sempre `false` neste contrato; não existe associação entre vistas implementada. |

O mesmo tratamento vale para detector, sintético e oracle. Não se infere
visibilidade por tamanho da caixa, classe, identidade anotada ou erro contra o
gabarito. Câmera, segmento, quadro, bbox e relógio são preservados. A última
observação histórica carrega a mesma incerteza, sem transportar uma posição
anterior aceita ou reconstruir contato por interpolação.

A planta mantém câmeras e cobertura, mas oculta projeções por padrão. A opção
"Projeções diagnósticas (chão não verificado)" mostra marcadores vazados,
tracejados e rotulados com `?`. Sua habilitação não altera `position_m` nem
autoriza associação. A interface informa contagem atual e histórica separadas;
somente pontos realmente exibidos participam da escala do mapa.

Esta entrega é uma abstenção explícita, não um detector de oclusões. Liberar
posições exige outro contrato versionado de evidência por observação, validação
de sua proveniência e medição de erro; permanece tarefa separada. Os resultados
históricos de projeção, incluindo o contraexemplo NVIDIA, não foram recalculados.

Validação do gate em CPU: um run por recorte oracle existente. As 132 observações
NVIDIA e 422 WILDTRACK conservaram a projeção diagnóstica e nenhuma foi liberada
para associação. Contagens, hashes e o caso Camera_0019/#6 em 2,5 s estão em
[`validation/ground-contact-gate-2026-11-07.json`](validation/ground-contact-gate-2026-11-07.json).
Isso comprova a abstenção do contrato, não a detecção de oclusões ou precisão de
localização. Não houve nova inferência nem download.

O fixture contém somente números/observações geradas e duas calibrações
sintéticas, sem arquivos de vídeo. A coincidência de posições entre vistas
foi construída; não demonstra associação de identidade ou desempenho de detector.

### 2. Mosaico e planta do laboratório virtual

Implementado em `multicamera_view.py`, `multicamera_media.py`,
`multicamera_server.py`, `assets/multicamera.*` e `scripts/serve_multicamera.py`.
O servidor escuta exclusivamente `127.0.0.1`; `--port` escolhe a porta. Não
substitui o painel operacional de uma câmera de `scripts/serve.py`.

- Acrescentar reprodução sincronizada com pausa e busca conjunta; começar pelos
  dados sintéticos do contrato, depois adaptar um recorte público autorizado.
- Mostrar estado e idade da imagem por câmera, instante do replay e cobertura
  conhecida. Exibir tracks locais com namespace, sem uni-los visualmente.
- Aceite: câmera atrasada, finalizada ou interrompida mantém indicação própria;
  uma imagem antiga não recebe marcador de tempo atual. Testar câmera ausente,
  lacuna, calibração ausente e IDs locais iguais em câmeras diferentes.
- Identificar em tela se posições vêm de oracle, fixture ou detector. Uma
  demonstração com posições anotadas valida a interface, não a percepção.

O relógio avança por `performance.now` durante a reprodução. A interface busca
snapshots até quatro vezes por segundo, com cancelamento de buscas anteriores,
e permite pausa, velocidade, retorno ao início e busca conjunta pelo teclado ou
slider. Quadros e posições não são interpolados. O mapa usa coordenadas em
metros; não exige um serviço de mapas externo nem uma imagem de planta.
Cobertura e posições de câmeras desconhecidas continuam explícitas.

Cada snapshot é calculado a partir do instante solicitado, sem carregar dados
do futuro depois de buscar para trás. Um quadro observado vale até o próximo
período nominal (`1/fps`); lacunas sem linha de cache tornam a câmera `stale`.
Linhas `missing` indicam ausência explícita. Depois de `end_s + 1/fps`, a
gravação está concluída. Não iniciada, ausente, sem observações e concluída não
exibem tracks como observações atuais. As últimas observações são opcionais no
mapa, tracejadas e com idade, sem afirmar onde a pessoa está agora.

As imagens são qualificadas por câmera, segmento e índice. A interface mostra
o tempo e a idade da imagem independentemente do cursor e acrescenta HISTÓRICO
nas lacunas e ao fim. Fixtures numéricos geram SVGs esquemáticos rotulados;
fontes `video` disponíveis localmente usam OpenCV para buscar o índice do quadro
e servir JPEG. Cada leitura abre e libera o decoder, sem estado compartilhado
entre requisições. Não são abertos streams RTSP por este laboratório.

A integridade do vídeo é conferida ao criar o catálogo de imagens; mudanças
posteriores de tamanho, mtime ou identidade do arquivo são recusadas. Não há
re-hash completo a cada quadro. Busca por índice depende do backend de vídeo:
um decoder que não confirme o índice é recusado. Foi testado MJPG gerado
localmente; não há garantia para todos os codecs ou desempenho em tempo real.
Imagens são reduzidas a no máximo 1280×720 para a tela. Sem o arquivo de vídeo,
o cache continua acessível e a imagem é declarada indisponível.

Recortes públicos NVIDIA e WILDTRACK já foram importados e reproduzidos, com
anotações em modo oracle. A projeção da base da bbox mostrou um contraexemplo
com pés ocluídos; não há aprovação geral de precisão geométrica. MTMMC foi
retirada do plano ativo do projeto pessoal. Não há benchmark de qualidade de detecção,
associação ou custódia multicâmera nesta interface.

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
