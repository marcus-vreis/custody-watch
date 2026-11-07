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

O avaliador verifica dimensões contra um quadro decodificado quando o vídeo
local está disponível, preservando as checagens de integridade da fonte. Em
fonte offline ou sintética, identifica explicitamente o domínio como não
verificado em vídeo. Não certifica visibilidade/precisão só por validar o JSON.

`scripts/evaluate_ground_contacts.py` produz contagens de cobertura,
abstenções e projeções dos pontos declarados, com hashes e proveniência. A
projeção da bbox é mantida separada. Não modifica caches, custódia ou replay;
`position_m` permanece nulo e `association_eligible` falso.

Referências de posição do gabarito servem exclusivamente à comparação oracle,
em resultados separados e rotulados. Não fornecem `status` de contato, confiança
de produção ou revisão humana. Sem medição independente e política explícita,
uma declaração de contato não libera posição para associação.

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
