# Evidência de contato com o chão — contrato v1

Decisão da #78, 2026-11-07: compor o cadastro existente com um arquivo de
evidências separado. Cadastro e sequências continuam estritos na versão 1;
a saída de replay v2 continua se abstendo de localizar/associar. A evidência
é uma declaração com proveniência, não uma certificação de precisão.

O arquivo contém exatamente `schema_version: 1`, `registry_sha256` e `cameras`.
Cada câmera contém `camera_id`, `segment_id`, `sequence_sha256`,
`image_size_px: [width,height]` e `observations`. As listas podem ser vazias;
ausência significa contato desconhecido, nunca observado.

Cada observação contém exatamente `frame_index`, `local_track_id`, `status`,
`point_px`, `provenance` e `method`. IDs referem-se à detecção existente naquele
quadro observado e segmento; não são identidades globais.

- `status`: `observed`, `occluded` ou `unknown`. `observed` declara um contato
  visível e exige um ponto explícito; os outros estados exigem `point_px: null`.
- `point_px`: par numérico finito no domínio `0 <= x < width`,
  `0 <= y < height` da imagem do arquivo, antes de reduzir a prévia. Não se
  presume o centro inferior da bbox. Um ponto na borda permanece diagnóstico.
- `provenance`: `human_review`, `synthetic` ou `model_prediction`. Evidência
  sintética só pode se referir a fonte/cache sintéticos. Uma estimativa de
  modelo não se converte em revisão humana por ter sido exportada neste formato.
- `method`: descrição não vazia do procedimento que originou a declaração.

Chaves extras/duplicadas, hashes incompatíveis, câmera/segmento/quadro/track
inexistentes, entradas duplicadas, tipos coercíveis e números não finitos são
recusados. O hash do cadastro vincula também calibrações, relógios e hashes de
sequências. Alterar qualquer entrada exige gerar outra evidência correspondente.

O avaliador verifica dimensões em cada quadro referenciado quando o vídeo
local está disponível, preservando as checagens de integridade da fonte. Em
fonte offline ou sintética, identifica explicitamente o domínio como não
verificado em vídeo. Não certifica visibilidade/precisão só por validar o JSON.

`verified_frame_indices` registra quais índices foram conferidos, uma vez por
quadro e câmera. Outros quadros recebem `image_bounds: not_checked`, sem herdar
a verificação do primeiro quadro ou de outra anotação. Uma mudança de resolução
em qualquer quadro referenciado recusa a avaliação antes de publicar a saída.

`scripts/evaluate_ground_contacts.py` produz contagens de cobertura,
abstenções e projeções dos pontos declarados, com hashes e proveniência. A
projeção da bbox é mantida separada. Não modifica caches, custódia ou replay;
`position_m` permanece nulo e `association_eligible` falso.

Referências de posição do gabarito servem exclusivamente à comparação oracle,
em resultados separados e rotulados. Não fornecem `status` de contato, confiança
de produção ou revisão humana. Sem medição independente e política explícita,
uma declaração de contato não libera posição para associação.

## Vínculo da imagem para revisão

A configuração do laboratório identifica `registry_sha256` e cada câmera
identifica `sequence_sha256`. O descriptor `image` repete esses hashes e contém
`tracks` somente do quadro de imagem indicado, inclusive quando histórico.
Uma imagem vazia não empresta os últimos tracks de outro quadro.

Os URLs novos de imagem incluem ambos os hashes. O servidor recusa hashes
incompatíveis, incompletos ou repetidos, impedindo reutilizar o mesmo URL depois
de trocar cadastro/sequência. URLs antigos sem o par continuam disponíveis para
compatibilidade de inspeção; a revisão/exportação requer o vínculo completo.
Os hashes não qualificam contato, calibração ou confiança de produção.

O núcleo de rascunhos (`assets/contact_review.js`) seleciona IDs somente desse
catálogo congelado e converte cliques pelo retângulo renderizado da imagem,
incluindo zoom e rolagem. Não arredonda/clampa pontos fora do domínio. O arquivo
exportado contém apenas os campos do contrato v1 e é aceito pelo mesmo leitor
Python, em testes de interoperabilidade executados com Node.js.

Guardar duplicatas exige remoção explícita da anotação anterior no rascunho.
Ambientes, segmentos, sequências e dimensões incompatíveis não são misturados.
O limite por página é de até 1000 anotações; exportar não apaga esse rascunho.
Origem e método são escolhas explícitas, sem herdar `ground_truth` do cache.
`human_review` requer declaração de revisão pessoal; isso é uma declaração do
operador, não autenticação de autoria ou certificação automática de confiança.

### Anotar no painel

1. Abra **Ampliar** na câmera e escolha um **ID do quadro exibido**. O replay
   pausa e a imagem permanece congelada durante a revisão.
2. Selecione **Observado**, **Ocluído** ou **Desconhecido**. Para observado,
   clique no contato na imagem ou preencha X e Y nos pixels originais indicados.
   A caixa e o marcador acompanham o zoom; ocluído/desconhecido exportam ponto nulo.
3. Escolha a origem, descreva o método e use **Guardar anotação**. Revisão
   pessoal exige confirmação explícita. A interface não promove estimativas
   automaticamente nem transforma a anotação em posição aceita.
4. Use **Exportar rascunho** no inspetor ou **Exportar evidências** no painel.
   Confira o download no navegador. A **Prévia do arquivo JSON** também permite
   consultar/copiar o conteúdo. O arquivo segue o contrato v1 acima.

O rascunho vive apenas na memória da página: sobrevive ao fechamento do inspetor
e às mudanças de câmera/tempo, mas é perdido ao recarregar ou fechar a página.
Alterações ainda não guardadas são descartadas ao trocar de ID ou fechar o
inspetor. Remover uma anotação é uma ação explícita e não modifica arquivos
já exportados. Exportar conserva o rascunho e não escreve no servidor;
o navegador controla o destino do download. Não há importação pelo painel.

A anotação exige vídeo com imagem decodificada e vínculos válidos; um esquema
sintético em SVG permite inspeção, mas não define pixels físicos da câmera.
IDs de outros quadros não são oferecidos como presentes na imagem congelada.
Falha no carregamento desabilita a anotação. A projeção exibida é a da bbox,
diagnóstica; não representa avaliação do contato contra um gabarito.

## Executar a avaliação offline

```powershell
.venv\Scripts\python.exe scripts/evaluate_ground_contacts.py `
  tests/fixtures/multicamera/two-cameras/registry.json `
  tests/fixtures/multicamera/two-cameras/ground-contacts.json `
  --output outputs/contact-fixture-novo
```

O destino precisa ser novo. `summary.json` identifica os hashes, as câmeras e
as contagens; `observations.jsonl` conserva cada observação em ordem global.
Contagem de rótulos inclui declarações explícitas desconhecidas; ausência
permanece contabilizada separadamente. Abstenção de projeção significa não
ter ponto projetável; abstenção de associação cobre todas as observações.

O avaliador é offline e mantém a saída das observações em RAM. Não oferece
retenção limitada para operação contínua. A fonte local precisa estar íntegra;
uma alteração de arquivo não é rebaixada silenciosamente a vídeo offline.
Dimensões conferidas não comprovam visibilidade, precisão da calibração ou
correção da declaração. Erro de validação/projeção ocorre antes de publicar o
diretório; falhas posteriores de escrita podem deixar saída parcial e são erro.

## Ensaio exploratório de 2026-11-07

Arquivos de declarações: `validation/ground-contacts-nvidia-2026-11-07.json`
e `validation/ground-contacts-wildtrack-2026-11-07.json`. São estimativas visuais
com `model_prediction`, sem revisão humana, fixadas antes de comparar com a
posição anotada. Não são exemplos de `human_review` nem treinamento validado.

Resultado: [`validation/contact-evidence-2026-11-07.json`](validation/contact-evidence-2026-11-07.json).
Um run por recorte oracle existente, sem seed aleatória; um contato comparável
por base. IDs são os do gabarito já presente nos caches, não associação calculada.

| Recorte | Observações | Declarações | Contatos comparados | Erro do contato estimado contra posição anotada |
| --- | ---: | ---: | ---: | ---: |
| NVIDIA | 132 | 2 | 1 | 0,019997079389693445 m |
| WILDTRACK | 422 | 2 | 1 | 0,4411485669091993 m |

Fonte de todas as contagens/erros: JSON acima, que também registra hashes de
cadastro, sequência, evidência, código e referência original. NVIDIA #6 conserva
contato ocluído/nulo; WILDTRACK #41 conserva contato desconhecido/nulo pela borda
inferior. As 554 observações permanecem sem posição aceita ou associação.

Esses dois pontos não estimam confiabilidade nem comparam bases/métodos.
A referência de posição do pedestre pode representar outro alvo físico que
o ponto entre apoios dos pés. A calibração fornecida não é validação física
independente. O arquivo fixa estimativas para reproduzir o cálculo, mas não
oferece um gerador de estimativas visuais reproduzível ou sua confirmação humana.
Nenhum ponto foi ajustado depois de consultar seu erro contra o gabarito.

O registro histórico foi gerado com o código identificado pelos hashes nele
gravados (disponível em `4264921`). A correção de domínio por quadro da #91 não
recalcula nem substitui esses resultados; novos runs devem usar outro arquivo.

Reprodução com os recortes e originais locais já obtidos; `--output` exige
arquivo novo. `<...>` indica caminhos a substituir, não download automático:

```powershell
.venv\Scripts\python.exe validation/measure_contact_cases.py `
  --nvidia-registry <recorte-nvidia/registry.json> `
  --wildtrack-registry <recorte-wildtrack/registry.json> `
  --nvidia-source <originais-nvidia> `
  --wildtrack-source <Wildtrack_dataset> --output <novo-resultado.json>
```

Os arquivos originais continuam fora do Git. Não houve inferência de detector,
novo peso, download ou GPU neste ensaio. Para liberar posições ainda falta
definir o alvo físico, revisar contatos e medir erro/cobertura/abstenções em
amostra adequada, com política explícita de incerteza espacial e temporal.
