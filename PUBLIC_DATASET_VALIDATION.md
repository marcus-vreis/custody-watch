# Validação de recortes públicos — 2026-11-06

**Importação, leitura de vídeo e replay executados em NVIDIA e WILDTRACK. MTMMC
não executada e retirada do plano ativo do projeto pessoal.** Os resultados completos, hashes,
revisões e limites estão no [registro versionado](validation/public-multicamera-2026-11-06.json).

Cada execução final usou duas câmeras e sete quadros por câmera, cobrindo três
segundos a 2 fps. São recortes de conveniência em modo **oracle**, com IDs e
caixas anotados. Não há treinamento, seed aleatória, inferência de detector,
associação calculada de identidades ou avaliação de custódia. O registro contém
um run final por base; as observações repetidas da mesma pessoa não são amostras
independentes. Não houve uso de GPU.

A retirada da MTMMC é uma decisão do mantenedor após revisar as exigências
acadêmicas do processo de acesso. O registro JSON da execução anterior permanece
preservado como histórico; não há solicitação de acesso planejada.

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

### Localizar o caso no painel

No laboratório com o recorte NVIDIA, abra
`/?t=2.5&camera=Camera_0019`. O link seleciona o andar, a câmera e o instante;
o tempo é limitado ao intervalo do replay. Câmera desconhecida e parâmetros
inválidos/repetidos mantêm os padrões do painel.

O quadro original `75` corresponde ao índice `5` do clip amostrado, em `2,5 s`.
A pessoa grande em primeiro plano é a anotação `24`; o caso é a anotação `6`,
quase encoberta atrás dela. No mesmo instante, os desvios são aproximadamente
`0,13 m` para `24` e `98,51 m` para `6`. Os valores e caixas estão no
[diagnóstico por anotação](validation/projection-case-6-2026-11-06.json).

Essas distâncias vêm da comparação offline com as coordenadas anotadas. O painel
atual mostra a projeção, mas ainda precisa de um inspetor que exponha essa
comparação. O vídeo pode parecer normal; o teste usa gabarito, sem inferência de
detector. Na miniatura do mosaico, o pequeno fragmento do ID `6` é difícil de ver.

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

## Declarações de contato — atualização de 2026-11-07

O gate conservador da #75 já mantém posições aceitas nulas. A #78 acrescenta
evidência separada por observação e avaliador offline, sem modificar este
resultado histórico ou liberar associação. Um novo ensaio exploratório usa um
contato estimado por base, com proveniência de modelo e sem revisão humana.
Contrato, números, reprodução e limites estão em [GROUND_CONTACTS.md](GROUND_CONTACTS.md)
e [validation/contact-evidence-2026-11-07.json](validation/contact-evidence-2026-11-07.json).
Essa pequena checagem do cálculo não estima confiabilidade de produção.

## Pendências

1. Validar evidências de contato e política de incerteza antes de qualificar posições.
2. Melhorar legibilidade dos rótulos da planta em cenas densas; há sobreposição.
3. Ampliar os recortes disponíveis, respeitando as condições de uso; MTMMC fica
   fora do plano ativo deste projeto pessoal.
4. Reproduzir percepção real da #56 e, separadamente, medir associação entre
   vistas e custódia com anotações adequadas. IDs comuns herdados do gabarito não
   demonstram re-identificação, e nenhuma anotação de bagagem/posse foi importada.
