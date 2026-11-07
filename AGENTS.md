# Instruções operacionais

As regras humanas e as autorizações já dadas na conversa prevalecem.
Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para fluxo, convenções e P1–P4;
[MULTICAMERA.md](MULTICAMERA.md) para escopo e contratos multicâmera; e
[PUBLIC_DATASET_VALIDATION.md](PUBLIC_DATASET_VALIDATION.md) para evidências e limites.

## Ambiente local e worktrees

- Preserve a `.venv` existente. Nunca rode `uv run`, `uv sync` ou `uv pip`
  sem pedido explícito; os exemplos de setup do CONTRIBUTING não autorizam isso.
- Use `.venv\Scripts\python.exe`, inclusive com `-m pytest` e `-m pre_commit`.
  A CI prepara seu próprio ambiente.
- No PowerShell, prepare o ambiente antes de testes e hooks; em worktree com
  venv compartilhada, `$repo` aponta para o checkout que contém essa venv:

```powershell
$repo = 'C:\Users\marcus\Documents\custody-watch'
$env:PATH = "$repo\.venv\Scripts;C:\Program Files\Git\usr\bin;$env:PATH"
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = Join-Path (Get-Location).Path 'src'
& "$repo\.venv\Scripts\python.exe" -c "import custody_watch; print(custody_watch.__file__)"
```

- Execute no worktree alvo e confira se `__file__` aponta para seu `src`;
  a instalação editable compartilhada pode apontar para o checkout principal.
- No Windows, passe globs ao filtro: `rg -g 'multicamera.*' src/custody_watch/assets`.

## Mudanças e verificação

- Uma tarefa por branch/PR. Bug: teste reproduzindo a falha antes da correção;
  feature: teste junto. Leia assert e traceback; nunca enfraqueça testes para passar.
- Rode testes do módulo e lint dos arquivos alterados durante o trabalho.
  Hooks são obrigatórios, com suíte completa no commit; nunca use `--no-verify`.
- Preserve P1–P4: posse só por vínculo forte; bagagem parada como âncora espacial;
  incerteza suprime alertas; flags descrevem relações, nunca atributos pessoais.
- Preserve fontes, dados e resultados. Toda métrica cita o arquivo de resultado
  e informa modo, split, seed (ou ausência) e quantidade de runs; não invente números.
  Nos recortes oracle atuais, IDs e caixas vêm do gabarito: não demonstram detector,
  associação calculada ou custódia. Avaliar lógica em oracle exige protocolo,
  algoritmo e anotações próprios; explicite o alcance.
- Antes de merge autorizado, obtenha SHA completo com `git rev-parse <ref>`
  e compare com o head remoto; use esse SHA em `--match-head-commit`.
  Nunca complete hash curto manualmente. Não amplie autorizações da conversa.
