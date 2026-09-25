# Arquitetura alvo

Este documento registra a arquitetura durável. Ele não é um diário de implementação.

## Objetivo

Monitorar reclamações do Reclame Aqui e identificar correspondências com produtos específicos de interesse em vigilância sanitária.

## Fluxo

1. Produto cadastrado.
2. Produto possui marca, fabricante, aliases e termos de busca.
3. O coletor executa buscas e preserva a reclamação original.
4. O motor de matching gera candidatos.
5. Regras determinísticas resolvem correspondências claras.
6. Fuzzy matching trata variações textuais.
7. IA é usada somente em ambiguidades justificadas.
8. Correspondências podem passar por validação humana.
9. Dashboard usa dados estruturados; LLM não calcula métricas.

## Camadas alvo

- `api`: contratos HTTP e validação.
- `services`: casos de uso/orquestração.
- `collectors`: integração com Reclame Aqui.
- `matching`: normalização, aliases, fuzzy e decisão.
- `repositories`: persistência.
- `models/domain`: entidades e schemas.
- `ai`: integração opcional para casos ambíguos.
- `frontend`: visualização e operação.

## Estrutura física

A Fase 0 estabeleceu a base mínima; os módulos das camadas acima são criados conforme
cada fase precisar deles, não antecipadamente.

- `app/config.py`: configuração por ambiente (`RAPM_*`) com caminhos resolvidos a partir da raiz do projeto.
- `app/db.py`: conexão DuckDB, lock de escrita e migrações versionadas em `schema_meta`.
- `app/main.py`: aplicação FastAPI, `/health` e tradução de exceções de domínio em status HTTP.
- `app/routers/`: contratos HTTP.
- `app/models.py`: schemas de entrada/saída.
- `app/repository.py`: persistência e identidade de produto.
- `app/collector.py`: coleta no Reclame Aqui — navegação Playwright e camada determinística.
- `app/collect_repository.py`: persistência de buscas, reclamações e sua procedência.
- `app/collect_service.py`: orquestração termo -> busca -> reclamações -> métricas.
- `app/matching.py`: motor determinístico de matching — camadas, limiares e evidência.
- `app/match_repository.py`: persistência da relação produto ↔ reclamação e da revisão.
- `app/match_service.py`: orquestração do reprocessamento sobre o escopo pedido.
- `app/taxonomy.py`: taxonomia fechada de produtos e vocabulário de contexto por categoria.
- `app/normalization.py`: forma canônica de texto, compartilhada entre cadastro e matching.
- `app/golden.py`: formato do Golden Dataset — leitura, escrita e validação do gabarito.
- `app/golden_export.py`: congela as validações humanas do banco em um Golden Dataset.
- `app/evaluation.py`: mede uma versão do motor contra um Golden Dataset (CLI e relatório).
- `app/analytics.py`: métricas do dashboard — agregações SQL, escopo comum e teste de variação.
- `frontend/index.html`: dashboard, página única sem framework, servida em `/` pela própria API.
- `golden/`: os conjuntos de referência, versionados. `glicosimetro-2026-09-25.json` é o
  conjunto permanente de regressão — 145 pares julgados por um revisor — contra o qual
  toda mudança de motor é medida (D-015).
- `tests/`: testes.
- `data/`: runtime local, não versionado.

## Entidades centrais

### Product
Produto monitorado e seus identificadores. Persistido em `product`, com aliases e termos de
busca em `product_term` (tipo `alias` ou `search`), cada termo guardando também sua forma
normalizada — é dela que as fases de busca e matching partem.

Identidade: chave natural derivada de nome + marca + modelo normalizados. Produto não é
removido; sai do monitoramento por desativação, preservando id, datas e termos.

### Complaint
Reclamação coletada, preservando fonte e conteúdo original. Persistida em `complaint`,
com a URL canônica, o texto como veio e o registro bruto da extração.

Identidade: o id que a fonte atribui à reclamação; quando ele não vem, a própria URL
canônica é a identidade. A URL pública é composta pelos campos da fonte
(`companyShortname` + `url`), nunca deduzida. Reclamação já conhecida não tem o conteúdo
reescrito por uma coleta posterior — só `last_seen_at` avança, para que a evidência da
primeira coleta permaneça intacta.

### ProductComplaintMatch
Relação auditável entre produto e reclamação, e entidade central do sistema. Persistida em
`product_complaint_match`, uma linha por par (produto, reclamação).

O registro responde "por que este produto foi associado a esta reclamação?": método
(camada que produziu a evidência), score, termo que casou, campo de origem, trecho original
citado, explicação em texto e a configuração de limiares que decidiu (`matcher_version`).

Decisão automática (`status`) e decisão humana (`reviewed_status`) são campos distintos; a
humana prevalece e não é sobrescrita por reprocessamento. Estados em `docs/DOMAIN.md`.

O reprocessamento é idempotente: o mesmo par produz o mesmo resultado, sem duplicar linha.
Par que deixa de ter evidência é datado em `stale_since` em vez de apagado — a evidência
que sustentou a associação continua disponível para auditoria.

Quando um produto mais específico identifica o aparelho, o par menos específico registra
`shadowed_by_product_id`; a partir do `det-3` ele deixa de ser candidato, e a linha que já
existia é datada com `stale_reason` — a regra que a tirou e a versão do motor que decidiu.
A evidência, a decisão automática anterior e a `matcher_version` que a produziu continuam
na linha: um par suprimido é auditável sem depender do histórico.

Decisão automática reescrita é preservada: antes do `UPDATE`, a anterior é copiada para
`product_complaint_match_revision` com a `matcher_version` que a produziu e a data em que
foi substituída (D-013). Trocar de versão do motor é, por isso, auditável — o histórico
cresce só quando a decisão muda de fato. Revisão humana não entra ali: ela não é reescrita
por reprocessamento.

Entre a busca e o match há uma separação deliberada: `search_hit` registra que uma busca
encontrou uma reclamação; só o matching afirma que ela é de um produto.

### Versão do motor
`MatchingConfig` reúne limiares, pesos e as regras ligáveis de cada versão; `version` entra
em cada match gravado. A versão anterior fica preservada como configuração executável
(`DET2`), de modo que comparar motores é rodar duas configurações sobre o mesmo conjunto,
não manter código antigo em paralelo. Uma mudança de motor só é avaliada contra rótulos
humanos, nunca contra impressão de quem mudou.

### Golden Dataset
Recorte datado de validação humana, versionado em `golden/` fora do banco: produtos,
reclamações e o parecer de cada par, suficiente para medir offline. O banco é estado vivo
e a revisão mais recente sobrescreve a anterior; o gabarito precisa ser fixo. `split`
separa o conjunto que originou as regras (`development`, mede regressão) do que mediria
desempenho (`holdout`), e a medição declara qual dos dois está fazendo. Um par congelado
em um conjunto não volta em outro.

### Métricas
Leitura agregada sobre as entidades acima, sem estado próprio: o dashboard não guarda nada,
recalcula a cada consulta. Um mesmo escopo — produto/categoria/subcategoria e recorte de
datas — atravessa todas as rotas, para que os números exibidos juntos se refiram ao mesmo
recorte, e volta na resposta como o backend o entendeu.

A data de referência da análise é a publicação da reclamação; em buscas, a execução. O que
não tem data legível na fonte não entra em série alguma e é contado à parte. "Reclamação de
um produto" é a que tem correspondência vigente e não descartada com ele — as mesmas
expressões SQL de `app/match_repository` decidem isso aqui, e a decisão humana prevalece na
contagem como prevalece no par.

Tendência tem duas condições, verificadas separadamente e devolvidas na resposta: cobertura
de coleta nas duas janelas e significância estatística (D-017). Sem as duas, a API devolve os
números e nenhuma direção. Nenhuma métrica é descrita, estimada ou interpretada por LLM.

### Search
Execução/termo que levou à descoberta de uma reclamação. Persistida em `search_run`: um
registro por termo consultado, aberto antes da coleta, de modo que uma busca que falha
também fica documentada.

O vínculo com as reclamações fica em `search_hit` (busca, reclamação, página, posição e se
aquela execução foi a que inseriu a reclamação). As contagens de `search_run` têm
significado fixo: `found` (itens detectados), `collected` (itens com URL real), `inserted`,
`duplicates` e `failures` (descartes de item mais falhas de página), com
`collected = inserted + duplicates`. `notes` é canal separado de `failures`: além das
falhas, registra o que foi gravado com perda de fidelidade (texto só disponível em prévia,
texto acima do limite) e o bloqueio que interrompeu a paginação.

## Modo de implantação

Somente máquina local, sem autenticação (D-018). A API escuta em `127.0.0.1`, só atende
`Host` listado em `RAPM_ALLOWED_HOSTS` (padrão `127.0.0.1`, `localhost`), recusa escrita
com `Origin` de outra origem e não habilita CORS. O dashboard é servido pela própria API,
na mesma origem.

## Restrições

- Fonte original não pode ser inventada.
- IA não substitui evidência.
- Decisões relevantes devem ser reproduzíveis/auditáveis.
