# RA Product Monitor

Sistema para localizar, relacionar e analisar reclamações do Reclame Aqui
associadas a produtos específicos de interesse em vigilância sanitária.

## Requisitos

- Python 3.12+ (validado em 3.14).

## Instalação

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS
```

`.env` é opcional (variáveis com prefixo `RAPM_`; modelo em `.env.example`).

Coleta no Reclame Aqui (Playwright) é um extra; a API e o dashboard sobem sem ele:
`pip install -e ".[collect]" && playwright install chromium`.

No Windows, se o caminho do projeto for longo, a instalação pode falhar por limite de
260 caracteres: habilite *long paths* ou use um diretório mais curto.

## Execução

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app
```

- Dashboard: <http://127.0.0.1:8000/>
- Documentação da API: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health> — `200` quando o banco responde, `503`
  caso contrário.

## Modo de implantação: somente máquina local

A API não tem autenticação. Por isso:

- escuta em `127.0.0.1` e só atende `Host` em `RAPM_ALLOWED_HOSTS`
  (padrão `["127.0.0.1","localhost"]`) — acesso por IP de rede ou DNS rebinding recebe `400`;
- recusa com `403` escrita (`POST`/`PATCH`) vinda de página de outra origem;
- não habilita CORS.

Expor na rede exige autenticação antes de ampliar `RAPM_ALLOWED_HOSTS` ou o `--host`.

## Testes

```bash
.venv/Scripts/python.exe -m pytest
```

## Caminhos

O banco DuckDB fica em `data/ra_product_monitor.duckdb`, resolvido sempre a partir da
raiz do projeto (não do diretório de trabalho). Ajustável por `RAPM_DATA_DIR` /
`RAPM_DB_FILENAME`.
