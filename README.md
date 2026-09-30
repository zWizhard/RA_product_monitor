# RA Product Monitor

Sistema para localizar, relacionar e analisar reclamações do Reclame Aqui associadas a
produtos de interesse em vigilância sanitária.

A peça central é a relação auditável **produto ↔ reclamação**: cada correspondência guarda
o método que a encontrou, o score, o trecho de evidência, a decisão automática e, quando
houver, a decisão humana. A reclamação original e o link para a fonte são sempre
preservados.

## Escopo

| Categoria | Subcategorias |
| --- | --- |
| Equipamentos | estética |
| Diagnóstico in vitro | glicosímetros |
| Materiais implantáveis | mamários, dentários |
| Materiais estéticos | PMMA, ácido hialurônico |

A taxonomia é fechada; incluir um valor novo é decisão de escopo (ver
[docs/DOMAIN.md](docs/DOMAIN.md)).

## Fluxo

```text
cadastrar produto → coletar reclamações → matching → revisar candidatos → consultar / acompanhar
```

## Funcionalidades

A interface web tem cinco abas:

- **Busca & coleta**: cadastro de produtos (nome, marca, fabricante, modelo, registro
  Anvisa, apelidos e termos de busca) e coleta no Reclame Aqui por termo livre ou pelos
  termos de um produto. Cada busca fica registrada, e cada reclamação guarda qual busca a
  encontrou. Opcionalmente, o matching roda logo após a coleta.
- **Reclamações**: consulta às reclamações já coletadas por termo, categoria, área e
  período. Uma etiqueta de relevância (relevante, duvidosa, provavelmente fora do assunto),
  calculada na hora por listas de palavras, organiza a leitura e destaca sinais de evento
  adverso. Nada é escondido.
- **Auditoria**: fila de candidatos produto ↔ reclamação, do maior para o menor score,
  para decisão humana (confirmar, possível, descartar) com revisor e nota. A decisão humana
  prevalece e sobrevive a reprocessamentos; o histórico mostra o que versões anteriores do
  motor decidiram.
- **Dashboard**: indicadores, variação contra o período anterior, evolução temporal,
  produtos mais citados e reclamações por situação na fonte. Todas as métricas são
  calculadas pelo banco.
- **Tutorial**: objetivo e passo a passo de cada funcionalidade.

O matching é determinístico, sem IA: normalização, nome/apelido exato, marca ou fabricante
com contexto, termos e sinônimos e, por último, fuzzy matching.

## Início rápido (Windows)

1. Descompacte o pacote em um caminho curto (ex.: `C:\RAPM`).
2. Clique duas vezes em `iniciar.bat`.

Na primeira execução ele oferece instalar o Python 3.12 se faltar e instala as
dependências e o Chromium. Nas seguintes, só sobe o servidor e abre o navegador em
<http://127.0.0.1:8000/>. Para encerrar, feche a janela.

## Instalação manual

Requisito: Python 3.12+ (validado em 3.14).

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS
```

`.env` é opcional (variáveis com prefixo `RAPM_`; modelo em `.env.example`).

Coleta no Reclame Aqui (Playwright) é um extra; a API e a interface sobem sem ele:
`pip install -e ".[collect]" && playwright install chromium`.

No Windows, se o caminho do projeto for longo, a instalação pode falhar por limite de
260 caracteres: habilite *long paths* ou use um diretório mais curto.

## Execução

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app
```

- Interface: <http://127.0.0.1:8000/>
- Documentação da API: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health> — `200` quando o banco responde, `503`
  caso contrário.

## Monitoramento semanal

Coleta todos os produtos ativos, executa o matching e grava um relatório Markdown em
`data/reports/relatorio-semanal-AAAA-MM-DD-exec-N.md`. Cada execução fica registrada na
tabela `monitor_run` (status, produtos, contagens, erros). Feche o servidor antes: o
DuckDB aceita um processo gravando por vez.

```bash
.venv/Scripts/python.exe -m app.monitor               # coleta + matching + relatório
.venv/Scripts/python.exe -m app.monitor --sem-coleta  # só o relatório dos últimos 7 dias
```

Agendamento no Windows (toda segunda às 8h; log em `data/logs/monitor.log`):

```bat
schtasks /create /tn "RA Product Monitor semanal" /sc weekly /d MON /st 08:00 /tr "\"C:\caminho\RA_Product_Monitor\monitorar.bat\""
```

Em servidor/container, agende o mesmo comando (`python -m app.monitor`) por cron ou
equivalente.

## Testes

```bash
.venv/Scripts/python.exe -m pytest
```

## Distribuição (Windows)

Com o servidor parado, gere o pacote com uma cópia do banco atual:

```powershell
powershell -ExecutionPolicy Bypass -File empacotar.ps1   # gera dist\RA_Product_Monitor.zip
```

O pacote leva `app/`, `frontend/`, `pyproject.toml`, este README, `iniciar.bat` e a cópia
do banco; não inclui `.venv`, `.env`, testes nem amostras. Cada máquina que o recebe fica
com seu próprio banco.

## Modo de implantação: somente máquina local

A API não tem autenticação. Por isso:

- escuta em `127.0.0.1` e só atende `Host` em `RAPM_ALLOWED_HOSTS`
  (padrão `["127.0.0.1","localhost"]`) — acesso por IP de rede ou DNS rebinding recebe `400`;
- recusa com `403` escrita (`POST`/`PATCH`) vinda de página de outra origem;
- não habilita CORS.

Expor na rede exige autenticação antes de ampliar `RAPM_ALLOWED_HOSTS` ou o `--host`.

## Dados

O banco DuckDB fica em `data/ra_product_monitor.duckdb`, resolvido sempre a partir da
raiz do projeto (não do diretório de trabalho). Ajustável por `RAPM_DATA_DIR` /
`RAPM_DB_FILENAME`. A pasta `data/` não é versionada.

## Estrutura

```text
app/            API FastAPI, coletor, matching, relevância e acesso ao banco
app/routers/    rotas: produtos, buscas, reclamações, matches, pesquisa, dashboard
frontend/       interface web (index.html, sem framework)
tests/          testes (pytest)
docs/           estado, arquitetura, domínio e decisões
golden/         conjunto de referência para avaliar o matching
iniciar.bat     instalação e execução em um clique (Windows)
empacotar.ps1   geração do pacote de distribuição
```

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): arquitetura.
- [docs/DOMAIN.md](docs/DOMAIN.md): taxonomia e regras de domínio.
- [docs/DECISIONS.md](docs/DECISIONS.md): decisões arquiteturais.
- [docs/STATE.md](docs/STATE.md): estado atual do projeto.
