# Inventário do RA Intelligence (Fase 0)

Fonte inspecionada: `RA/` (referência local, não versionada, não importada por `app/`). Registro histórico: `RA/` foi apagado em 2026-09-25 e o banco antigo não foi migrado.

| Componente | Arquivo original | Decisão |
|---|---|---|
| Scraper Reclame Aqui (Playwright) | `RA/backend/scraper.py` | Reutilizar com correção na fase de coletor: remover link sintético inventado quando não há href real; extração de cidade/status por seletor frágil precisa revisão. |
| Persistência DuckDB | `RA/backend/database.py` | Reescrever. Schema é orientado a "reclamação classificada", não à relação `product <-> complaint`. Reaproveitar apenas o padrão de conexão e caminho por variável de ambiente. |
| Schemas Pydantic | `RA/backend/models.py` | Reescrever quando as entidades de domínio entrarem. |
| API FastAPI | `RA/backend/main.py` | Reescrever. Camadas misturadas (rotas + jobs + scraping + CSV), CORS `*`, imports no fim do arquivo, jobs em memória. |
| IA (Anthropic) | `RA/backend/ai_service.py` | Reutilizar apenas o padrão de chamada HTTP; modelo desatualizado e saída sem validação de schema. |
| Regras GHBIO/categorias | `RA/backend/ghbio_modules.py`, `CATEGORY_KEYWORDS` em `scraper.py` | Avaliar como insumo de taxonomia em `docs/DOMAIN.md`; não transportar como código nesta fase. |
| Frontend | `RA/frontend/index.html` | Descartar. Arquivo único de 83 KB acoplado à API antiga. |
| Diagnóstico do scraper | `RA/backend/diagnostico_scraper.py` | Descartar. |
| Versões legadas | `main_old.py`, `main_v1.py`, `main_v2.py`, `ai_service_v1.py`, `ai_service_v2.py` | Descartar. |
| Banco de produção antigo | `RA/backend/ra_intelligence.duckdb(.wal)` | Não transportar. Preservado em `RA/` para eventual migração futura autorizada. |
| `venv` e `__pycache__` | `RA/backend/venv`, `__pycache__` | Não transportar. O venv antigo aponta para Python 3.12, ausente na máquina. |

## Dependências

Base nova fixada em `pyproject.toml`: FastAPI, Uvicorn, DuckDB, Pydantic, pydantic-settings,
python-dotenv. Playwright fica no extra `collect`; pytest e httpx no extra `dev`.
Todas instalam em Python 3.14 (única versão disponível na máquina).
