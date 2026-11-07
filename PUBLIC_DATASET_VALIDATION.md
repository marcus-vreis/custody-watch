# Validação de recortes públicos — 2026-11-06

**Importação, leitura de vídeo e replay executados em NVIDIA e WILDTRACK. MTMMC
não executada: depende de acesso autorizado.** Os resultados completos, hashes,
revisões e limites estão no [registro versionado](validation/public-multicamera-2026-11-06.json).

Cada execução final usou duas câmeras e sete quadros por câmera, cobrindo três
segundos a 2 fps. São recortes de conveniência em modo **oracle**, com IDs e
caixas anotados. Não há treinamento, seed aleatória, inferência de detector,
associação calculada de identidades ou avaliação de custódia. O registro contém
um run final por base; as observações repetidas da mesma pessoa não são amostras
independentes. Não houve uso de GPU.

| Base | Recorte executado | Resultado |
| --- | --- | --- |
| NVIDIA PhysicalAI-SmartSpaces 2025 | `Warehouse_000`, `Camera_0008`/`Camera_0019`, quadros originais `0,15,…,90` a 30 fps | Importação/replay passaram; encontrado contraexemplo à suposição de contato com o chão na base da caixa visível. |
| WILDTRACK | `C1`/`C2`, imagens `00000000` a `00000030`, passo 5 no relógio de 10 fps | Importação/replay passaram; desvios de projeção registrados abaixo. |
| MTMMC | Nenhum arquivo de dados autorizado fornecido nesta sessão | Não executada; zero runs. O [site oficial](https://sites.google.com/view/mtmmc) exige acordo assinado e aprovação. Nenhum contato ou submissão foi realizado. |

## O que foi conferido

- Conversão para os manifests de sequência e cadastro existentes, preservando
  `mode: oracle`, `provenance: ground_truth` e namespace de câmera/segmento.
- Busca e leitura dos índices originais em vídeo, dimensões e fps. As imagens
  selecionadas foram reencodificadas em clips MJPG locais a 2 fps; o tempo
  original está registrado. Quadros intermediários não foram inventados.
- Leitura de JPEGs do início e fim de cada clip pelo mesmo backend do painel.
- Mosaico e planta no navegador, rótulos de oracle e precisão de sincronização
  desconhecida. A sincronização é declarada pelas bases; seu erro não foi medido.
- Desvio entre a projeção do centro inferior da bbox e o ponto de mundo anotado,
  usando o `GroundPlane` efetivamente carregado pelo projeto.

O mapa de fundo `map.png` da NVIDIA foi obtido e teve o hash conferido, mas não
é usado como imagem de fundo do painel. A planta atual usa eixos métricos.
Cobertura permanece desconhecida, em vez de ser estimada a partir das pessoas.

## Resultado geométrico e limite encontrado

Valores em metros, incluindo caixas que tocam a borda. Fonte de todos os números:
[registro da execução](validation/public-multicamera-2026-11-06.json), campos
`datasets.*.cameras[].bbox_foot_to_world_annotation`.

| Base / câmera | Observações | Mediana | P95 | Máximo |
| --- | ---: | ---: | ---: | ---: |
| WILDTRACK C1 | 232 | 0,107 | 0,183 | 0,218 |
| WILDTRACK C2 | 190 | 0,078 | 0,235 | 0,297 |
| NVIDIA Camera_0008 | 35 | 0,107 | 18,672 | 18,677 |
| NVIDIA Camera_0019 | 97 | 0,197 | 11,503 | 98,513 |

Estes desvios **não são erro puro da calibração**. Eles incluem a escolha da
base da bbox, oclusões, truncamento e convenções das anotações. O relatório
também separa caixas que não tocam as bordas; isso não elimina oclusão interna.
Não são métricas de detector, tracking ou localização geral em um terminal.

O contraexemplo NVIDIA é a anotação `6`, câmera `Camera_0019`, quadro `75`.
A caixa visível tem `ymax-ymin` de apenas nove pixels; o ponto de chão anotado,
reprojetado na imagem, fica fora dela. A imagem mostra o personagem ocluído por
outro. Projetar a base dessa caixa no plano do chão produz o desvio próximo de
98,51 m. Dados e coordenadas do diagnóstico estão no campo
`datasets.nvidia.diagnostic` do registro.

**Consequência para o projeto:** uma homografia numericamente consistente não
garante que a base de toda bbox esteja no chão. Antes de associar pessoas entre
câmeras ou transportar evidência de posse, falta uma política para recusar ou
marcar como incerta a posição quando não há contato visível confiável. Essa
política ainda não foi implementada. O painel agora explicita a origem das
posições e avisa sobre esse limite.

As correspondências usadas para converter a calibração ao formato do projeto
foram geradas algebricamente das matrizes publicadas. O resíduo do carregador
verifica essa conversão; não constitui uma medição física independente.

## Proveniência e aquisição

- [NVIDIA](https://huggingface.co/datasets/nvidia/PhysicalAI-SmartSpaces): revisão
  fixada no registro. A ficha declara CC BY 4.0. Dois vídeos RGB foram baixados
  integralmente e conferidos contra os SHA-256 LFS publicados; calibração e mapa
  também foram conferidos. Do ground truth foi baixado somente um prefixo de
  8.000.000 bytes, suficiente para os frames solicitados. Seu próprio hash foi
  registrado; **o hash do arquivo completo de ground truth não foi verificado**.
  A seleção das câmeras minimizou o volume de vídeo entre pares com pelo menos
  três IDs anotados em comum no primeiro quadro; não é um split de avaliação.
- [WILDTRACK](https://www.epfl.ch/labs/cvlab/data/data-wildtrack/): extração parcial
  do ZIP oficial por HTTP Range, com CRC dos membros e hashes locais registrados.
  Não foi baixado o arquivo integral. A aquisição precisou tratar offsets de
  cabeçalhos do ZIP legado, conferindo assinatura e CRC. A conversão de grade
  e centímetros segue a [implementação publicada do MVDet](https://github.com/hou-yz/MVDet/blob/master/multiview_detector/datasets/Wildtrack.py).
  Não foi encontrada licença explícita dos dados na página consultada ou nos
  membros selecionados; nenhum vídeo, imagem ou anotação original foi incluído no Git.
- [MTMMC](https://sites.google.com/view/mtmmc): somente processo de acesso e
  descrição pública revisados. Isso não equivale a validar o projeto na base.

O código registra revisão Git com sufixo `+dirty` quando necessário, hash do
importador e hashes dos arquivos utilizados. As mídias ficam fora do Git.

## Reexecutar com dados locais

O importador não baixa arquivos nem aceita acordos. Para NVIDIA, espera
`calibration.json`, vídeos `Camera_NNNN.mp4` e `ground_truth.json` ou um
`ground_truth.prefix.json.part` contendo integralmente os frames solicitados.
Para WILDTRACK, espera a estrutura publicada com `Image_subsets/`,
`annotations_positions/` e `calibrations/intrinsic_zero`/`extrinsic`.

```powershell
.venv\Scripts\python.exe scripts/import_multicamera_sample.py nvidia data/nvidia --output outputs/nvidia-sample --frames 0,15,30,45,60,75,90 --cameras Camera_0008,Camera_0019 --source-revision REVISAO_DA_FONTE
.venv\Scripts\python.exe scripts/import_multicamera_sample.py wildtrack data/Wildtrack_dataset --output outputs/wildtrack-sample --frames 0,5,10,15,20,25,30 --cameras C1,C2
.venv\Scripts\python.exe scripts/serve_multicamera.py outputs/wildtrack-sample/registry.json
```

Use diretórios novos. Uma falha de leitura/escrita pode deixar um recorte parcial;
a ausência de `validation.json` com sucesso impede tratá-lo como execução válida.

## Pendências

1. Recusar ou qualificar projeções sem contato confiável com o chão.
2. Melhorar legibilidade dos rótulos da planta em cenas densas; há sobreposição.
3. Obter acesso autorizado e executar a MTMMC com os arquivos reais.
4. Reproduzir percepção real da #56 e, separadamente, medir associação entre
   vistas e custódia com anotações adequadas. IDs comuns herdados do gabarito não
   demonstram re-identificação, e nenhuma anotação de bagagem/posse foi importada.
