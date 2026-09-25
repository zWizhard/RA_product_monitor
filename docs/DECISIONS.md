# Decisões arquiteturais

## D-001 — Derivar um novo projeto do RA Intelligence

**Decisão:** reutilizar componentes técnicos úteis do RA Intelligence em um novo projeto, em vez de continuar adaptando o sistema antigo diretamente.

**Motivo:** preservar FastAPI, Playwright, DuckDB e padrões úteis sem carregar lógica GHBIO antiga, versões duplicadas e contratos inconsistentes.

**Alternativa rejeitada:** iniciar totalmente do zero.

**Consequência:** a Fase 0 deve inventariar, copiar e limpar seletivamente a base.

---

## D-002 — Matching híbrido com IA como última camada

**Decisão:** usar normalização, correspondência exata, aliases e fuzzy matching antes de LLM.

**Motivo:** reduzir custo, aumentar auditabilidade e tornar resultados mais reproduzíveis.

**Alternativa rejeitada:** enviar toda reclamação diretamente para uma LLM.

**Consequência:** o sistema precisa registrar método, score e evidência do match.

---

## D-003 — Reescrever o backend em vez de portar o do RA Intelligence

**Decisão:** do RA Intelligence só o scraper Playwright é candidato a reaproveitamento de código (após correção); API, persistência, schemas e frontend são reescritos.

**Motivo:** o backend antigo mistura rotas, jobs, scraping e exportação num único módulo, e seu schema é orientado a "reclamação classificada", não à relação `product <-> complaint`, que é a entidade central deste sistema.

**Alternativa rejeitada:** copiar o backend antigo e adaptá-lo incrementalmente.

**Consequência:** D-001 fica restrita a padrões técnicos e ao coletor; o banco antigo permanecia em `RA/` e uma eventual migração de dados exigia autorização explícita. `RA/` foi apagado pelo usuário em 2026-09-25, sem migração.

---

## D-004 — Runtime com caminhos previsíveis a partir da raiz do projeto

**Decisão:** configuração via `pydantic-settings` com prefixo `RAPM_`; o banco fica em `data/ra_product_monitor.duckdb`, resolvido a partir da raiz do projeto, e caminhos relativos vindos do ambiente também são resolvidos contra ela.

**Motivo:** no sistema antigo o banco era criado onde o processo fosse iniciado, gerando bancos duplicados conforme o diretório de trabalho.

**Alternativa rejeitada:** caminho relativo ao diretório de trabalho, como no RA Intelligence.

**Consequência:** o estado coletado tem um único local previsível; `data/` não é versionado.

---

## D-005 — Produto identificado por chave natural e desativado, nunca removido

**Decisão:** a identidade de um produto é a chave natural derivada de nome + marca + modelo
normalizados, e a saída do monitoramento é feita por desativação (`active = false`).

**Motivo:** a entidade central do sistema é a relação auditável `product <-> complaint`.
Remover um produto apagaria o outro lado de correspondências já decididas; e sem uma chave
estável o mesmo produto entraria duas vezes com grafias diferentes, dividindo as evidências.

**Alternativa rejeitada:** identidade só pelo id gerado, com `DELETE` para retirar produtos
do escopo.

**Consequência:** a API não expõe remoção; o cadastro rejeita duplicata com 409 em vez de
criar um segundo registro. `product_term` não declara FOREIGN KEY: no DuckDB, atualizar
coluna indexada do pai equivale a delete+insert e quebraria a restrição a cada edição de
nome, marca ou modelo — a integridade fica no repositório, que nunca apaga produtos.

---

## D-006 — Reclamação identificada pela fonte; URL nunca é sintetizada

**Decisão:** a identidade de uma reclamação é o id extraído da URL real da fonte, ou a URL
canônica quando ela não carrega id. Item sem link utilizável, com link fora do domínio do
Reclame Aqui ou cujo caminho não seja de reclamação é descartado e contado como falha. O
conteúdo da primeira coleta não é reescrito por coletas posteriores.

**Motivo:** a rastreabilidade até a fonte original é o que dá valor probatório à relação
`product <-> complaint`. O scraper do RA Intelligence fabricava um link quando não
encontrava o href, produzindo registros que não podem ser verificados nem deduplicados.

**Alternativa rejeitada:** aceitar o item com URL sintética ou identificador gerado
localmente, preservando a contagem de coleta.

**Consequência:** a coleta pode devolver menos itens do que a página exibe, e isso é
visível em `failures`/`notes` em vez de silencioso. Se o formato da URL mudar, a busca
descarta tudo de forma observável — falha ruidosa preferida a dado errado gravado.

---

## D-007 — Navegação separada da extração determinística

**Decisão:** o navegador apenas extrai registros brutos do DOM; validação de URL,
normalização e montagem da reclamação são funções puras, fora do Playwright.

**Motivo:** o Reclame Aqui monta a lista no cliente, então não há HTML estático para
analisar; sem essa separação, nenhuma regra de coleta seria testável sem rede e sem
navegador, e a proibição de inventar URL não teria como ser verificada.

**Alternativa rejeitada:** extrair e validar dentro do próprio código Playwright, como no
scraper antigo.

**Consequência:** a suíte cobre identidade, descarte e métricas sem acessar a internet; o
que fica sem cobertura automática é apenas a camada de seletores, cuja quebra é reportada
em `failures`/`notes`.

---

## D-008 — Match preservado: obsolescência datada e precedência da decisão humana

**Decisão:** a identidade de uma correspondência é o par (produto, reclamação), com no
máximo uma linha. O reprocessamento reescreve só a decisão automática; a decisão humana e
sua justificativa nunca são sobrescritas. Par que deixa de ter evidência é datado em
`stale_since`, não removido.

**Motivo:** a relação `product <-> complaint` é a entidade central e o que dá valor ao
sistema é poder explicar, depois, por que ela existiu. Apagar a linha quando o cadastro
muda destruiria a evidência que sustentou a associação e apagaria junto o trabalho de
validação já feito.

**Alternativa rejeitada:** recriar as correspondências a cada execução, apagando as
anteriores — mais simples, mas transforma reprocessar em perder histórico.

**Consequência:** reprocessar é seguro e idempotente, e a consulta precisa distinguir
match vigente de obsoleto (`stale`). Em troca, a tabela guarda associações que já não são
verdadeiras pela regra atual, e isso precisa ficar visível na análise e no frontend.

---

## D-009 — Limiares versionados e confirmação só por identidade forte

**Decisão:** limiares, pesos e versão do matching ficam em uma única configuração
(`MatchingConfig`), gravada em cada match. Confirmação automática exige nome ou alias do
produto no texto; marca com contexto, termo de busca e fuzzy produzem apenas candidato
para revisão. Nesta fase nenhuma camada de IA foi necessária.

**Motivo:** `docs/DOMAIN.md` exige que termo genérico não confirme correspondência, e um
match só é auditável se for possível saber com que limiares ele foi decidido — senão,
ajustar um peso reescreve silenciosamente o significado do que já está gravado.

**Alternativa rejeitada:** limiares embutidos em cada camada e ajustados por tentativa,
sem registro de versão.

**Consequência:** mudar limiar é mudança rastreável de versão, e a fila de revisão humana
é parte normal da operação, não exceção. A camada de IA continua reservada para
ambiguidade que as regras não resolvam, e entra com saída validada e prompt rastreável.

---

## D-010 — Pendência derivada e revisão sem versionamento

**Decisão:** a fila de validação humana não tem estado nem coluna próprios: um par está
pendente quando a decisão que vale ainda é `possible` e a evidência está vigente. A
revisão grava status, validador, data e nota na própria linha do match; uma revisão
posterior substitui a anterior, sem guardar as revisões passadas.

**Motivo:** o estado pendente é consequência das duas decisões já gravadas (automática e
humana); materializá-lo criaria uma terceira fonte de verdade que pode divergir delas a
cada reprocessamento. Guardar histórico de revisões é outra entidade, e a auditoria
exigida nesta fase é sobre a decisão automática, que permanece intacta.

**Alternativa rejeitada:** coluna `queue_status` mantida por gatilho ou pelo serviço de
matching, e tabela de histórico de revisões desde já.

**Consequência:** a fila é uma consulta sobre `product_complaint_match`, sem migração de
schema e sem risco de ficar dessincronizada. Em troca, só a última revisão de cada par é
conhecida: se rastrear quem mudou de opinião virar requisito, será preciso uma tabela de
histórico e uma migração.

---

## D-011 — Extração pela resposta JSON da busca, capturada na própria sessão

**Decisão:** a coleta deixa de ler o DOM da página de busca e passa a usar a resposta do
endpoint que a própria página consulta
(`iosearch.reclameaqui.com.br/raichu-io-site-search-v1/query/{termo}/{tamanho}/{offset}`),
capturada por evento do Playwright durante a navegação. A paginação segue o `offset` desse
endpoint, limitada pelo `count` que a resposta declara, e para no primeiro bloqueio. A URL
pública é composta por
`companyShortname` + `url` do registro; faltando qualquer um dos dois, o registro é
descartado e contado como falha. O texto gravado é `descriptionMasked` quando ele é o mais
completo — `description` vem truncada em ~130 caracteres e o campo mascarado traz a
reclamação inteira com dados pessoais já substituídos por `****` na origem; ambos
permanecem em `raw`.

**Motivo:** a página de busca não renderiza reclamação nenhuma no DOM (zero ocorrências de
`/reclamacao/` no HTML), então os seletores herdados do RA Intelligence não tinham o que
extrair. Chamar o endpoint fora da sessão do navegador devolve 403, e capturar a resposta
que a navegação já provoca não acrescenta requisição nem contorna proteção.

**Alternativa rejeitada:** chamar o endpoint diretamente com os cabeçalhos da sessão, e
abrir a página de cada reclamação para recuperar o texto completo — uma esbarra no 403, a
outra multiplica requisições sem necessidade, já que o texto completo vem na própria busca.

**Limite verificado (17/09/2026, revisto em 21/09/2026):** o bloqueio é da **segunda
navegação dentro da mesma sessão de navegador**, não do termo nem do site como um todo. A
segunda navegação recebe o desafio do Cloudflare (403, "Um momento…") e o documento sequer
consulta o endpoint — com 4 s e também com 60 s de intervalo. As duas saídas que restariam
(`fetch` do próprio documento para outro `offset`, barrado por CORS; requisição pela pilha
de rede do navegador fora da página, 403) são tentativas de contornar a proteção e por isso
não foram adotadas.

Como cada termo abre um navegador próprio, **buscas independentes não se afetam**: em
21/09/2026, 8 termos seguidos concluíram normalmente, 10 itens cada, sem nota de bloqueio.
A limitação é, portanto, de **paginação**: `pages > 1` reusa a sessão, cai no desafio, e o
coletor detecta, anota e interrompe. Ampliar cobertura hoje significa mais termos, não mais
páginas.

**Consequência:** o parse passa a ter contrato de dados (campos nomeados), não de seletor:
mudança de formato aparece como `failures`/`notes`, e o fixture de resposta real cobre
parse, descarte, composição de URL e deduplicação sem rede. Em troca, a coleta fica
acoplada a um endpoint interno do site: se ele mudar de caminho ou de formato, a busca
falha de forma ruidosa, em vez de gravar dado errado. O ritmo entre requisições é
requisito, e vale por coletor — inclusive entre termos de uma mesma busca, que antes
encadeavam navegações sem pausa. Perda de fidelidade do conteúdo (texto só disponível em
prévia, texto acima do limite gravável) entra como nota da execução, separada de
`failures`: registro gravado incompleto em silêncio seria o pior dos dois mundos.



---

## D-012 — `det-2`: identificação sob texto espontâneo de consumidor

**Decisão:** o motor determinístico ganha quatro regras, todas na camada determinística, e
a versão passa a `det-2`:

1. **Arbitragem por especificidade.** Quando o nome/alias de dois produtos casa sobre
   trechos sobrepostos do mesmo campo e um é subsequência estrita do outro, o menos
   específico é marcado em `shadowed_by_product_id` e deixa de confirmar sozinho. Produtos
   citados em trechos distintos não se arbitram.
2. **Evidência exclusiva na descrição.** Continua sendo evidência válida e continua
   confirmando — inclusive quando o modelo só aparece no corpo. O que ela não sustenta
   sozinha é a confirmação automática em dois casos: menção comparativa imediatamente antes
   da ocorrência (lista fechada de sinais como "era usuário", "outro modelo") ou marca
   incompatível com a que a empresa da página atribui.
3. **Grafia composta.** Termo do catálogo é comparado também na forma sem separadores, por
   junção de tokens inteiros: "One Touch"/"OneTouch", "G-Tech"/"GTech",
   "Accu-Chek"/"Accuchek". Nunca casa dentro de um token maior.
4. **`company` como evidência estrutural.** A empresa da página é atribuição de marca da
   fonte, não texto do consumidor: vale como marca quando corresponde ao catálogo,
   qualificada por contexto de categoria, e nunca confirma um modelo sozinha
   (`company_brand`, teto em `possible`). Empresa fora do catálogo — farmácia, marketplace —
   não atribui marca nenhuma.

**Motivo:** o confronto com 75 reclamações reais mostrou que `det-1` errava por três vias
mensuráveis — família confirmando junto com o modelo (12 pares), aparelho citado por
comparação confirmando como se fosse o reclamado (25 das 58 confirmações vinham da
descrição) e marca perdida por variação de grafia ou por só existir no nome da empresa
(9 reclamações sem match nenhum). Nenhuma delas exige interpretação de linguagem natural.

**Alternativa rejeitada:** penalizar toda evidência de descrição com um peso fixo, simétrico
ao bônus de título. Rebaixaria também o caso legítimo — o modelo que só aparece no corpo —
e trocaria um erro de precisão por um de cobertura. Também foi rejeitado transformar
vocabulário de categoria ("sensor de glicose") em alias de produto: ele descreve o tipo,
não identifica modelo, e por isso mora em `app/taxonomy.py`, qualificando marca.

**Consequência:** sobre o mesmo corpus, `confirmed` caiu de 58 para 42 e `possible` subiu de
55 para 103, com as reclamações sem match caindo de 10 para 3 e nenhum par perdido. O
sistema passou a errar para o lado da fila de revisão, que é onde o erro é corrigível. Cada
rebaixamento fica explicado na própria linha do match. O custo por par subiu para ~11 ms,
dos quais ~9 ms são do fuzzy; a comparação compactada não pesa porque cada reclamação é
indexada uma vez e consultada por todos os produtos. Reprocessamento fatiado por produto
passou a avaliar (sem gravar) os demais produtos ativos: sem os vizinhos, a arbitragem não
existe e o candidato rebaixado voltaria a confirmar.

---

## D-013 — Proveniência da decisão automática por versão do matcher

**Decisão:** antes de um reprocessamento reescrever método, score, evidência ou status de um
par, a decisão anterior é copiada para `product_complaint_match_revision`, junto da
`matcher_version` que a produziu e da data em que foi substituída. O histórico é exposto em
`GET /matches/{id}/revisions`.

**Motivo:** `matcher_version` sozinha diz com que configuração a decisão *atual* foi tomada,
mas a linha é sobrescrita: trocar de versão apagaria o que a versão anterior havia decidido
e com qual evidência. Sem isso, a troca de `det-1` para `det-2` seria uma alteração
destrutiva em 113 associações já gravadas.

**Alternativa rejeitada:** versionar a própria tabela de matches (uma linha por versão por
par), o que quebraria a unicidade do par e obrigaria toda consulta a filtrar pela versão
vigente. Também foi rejeitado confiar em cópia do arquivo do banco: backup não é
proveniência consultável, e o arquivo fica travado pelo servidor em execução.

**Consequência:** a reexecução em `det-2` preservou as 113 decisões de `det-1` com sua
evidência íntegra, e o histórico cresce só quando a decisão automática muda de fato —
reprocessamento idempotente não gera revisão. A revisão humana continua fora daí: ela não é
reescrita por reprocessamento e segue com a limitação de D-010.


---

## D-014 — `det-3`: precedência do modelo, supressão de candidato e conjunto rotulado

**Decisão:** quatro regras entram no motor determinístico, nesta ordem obrigatória, e a
versão passa a `det-3`:

1. **Precedência do nome mais específico.** Título e corpo são lidos juntos e a ordem dos
   tokens deixa de decidir ("libre plus 2" = "Libre 2 Plus", por janela reordenada de dois
   ou mais tokens adjacentes, mesma quantidade). O produto cujo nome está contido no de
   outro perde a disputa na reclamação inteira, não só no mesmo trecho — inclusive quando
   o que casou foi o **modelo em posição de contexto** ("Guide" dentro de "Smart Guide").
2. **Supressão do vencido.** Quem perde a especificidade deixa de ser candidato.
3. **Irmão da mesma marca.** Identificado um modelo pelo nome, outro produto da mesma
   marca que só tenha marca+contexto não é candidato. Roda **depois** da arbitragem,
   contando apenas os vencedores dela.
4. **Citação por comparação, ampliada.** Mais 19 sinais de aferição e comparação ("da
   mesma marca", "obrigado a comprar", "para comparar", "em paralelo", "simultâneo com").

As quatro são chaves em `MatchingConfig`. Desligá-las reproduz o `det-2`, preservado
executável na constante `DET2` — comparar versões não depende de código antigo guardado
em paralelo. Candidato suprimido carrega `suppressed_by`, e a linha que se torna obsoleta
grava `stale_reason` com a regra e a versão que a tiraram.

**Motivo:** os 145 pares do corpus real foram julgados um a um por um revisor
(24–25/09/2026). Contra esse gabarito, o `det-2` confirmava 30 acertos e 10 erros, e
mandava 103 pares para revisão humana — dos quais apenas 4 eram o produto. Os erros não
exigiam interpretação de linguagem: eram a família absorvendo o modelo citado em outro
trecho, o irmão da marca entrando de carona, e o aparelho citado como régua de aferição.

**Medição sobre os 145 rótulos** (positivo = o motor confirmou; os 25 pares que o revisor
deixou em dúvida ficam fora de TP/FP/FN/TN):

| | TP | FP | FN | TN | Precisão | Recall | F1 | Revisão |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `det-2` | 30 | 10 | 4 | 76 | 0,750 | 0,882 | 0,811 | 103 |
| `det-3` | 32 | 5 | 2 | 81 | 0,865 | 0,941 | 0,901 | 47 |

Cinco falsos positivos removidos, **nenhum verdadeiro positivo perdido**, nenhum falso
positivo novo, nenhum par criado fora do conjunto conhecido.

Isolada sobre o `det-2`, cada regra: **1** TP 32 / FP 7 / revisão 104 — a única que move
acerto; **2** métrica inalterada, revisão 91; **3** métrica inalterada, revisão 59; **4**
FP 8, revisão 105. Acumulado na ordem obrigatória: 1 → F1 0,877 · 1+2 → 0,877 · 1+2+3 →
0,877 (revisão 45) · 1+2+3+4 → 0,901 (revisão 47). Ou seja: as regras 2 e 3 não corrigem
decisão, esvaziam fila; a regra 4 troca duas confirmações erradas por dois itens de
revisão.

**Supressões, com rótulo humano:** especificidade — 18 pares, os 18 descartados pelo
revisor; irmão de marca — 41 pares, 38 descartados e 3 em dúvida. Nenhuma supressão
alcançou par que o revisor tenha confirmado, e isso é teste, não observação.

**Caso sentinela #58** (c41): o corpo diz "freestyle libre plus 2" e o cadastro tem
"FreeStyle Libre 2 Plus". No `det-2` o par só existia por marca+contexto, e a regra 3 —
sozinha — o teria removido, porque quem estava "nomeado" na reclamação era a família
errada. Com a regra 1 ele passa a ser identificado pelo nome e sobrevive às seguintes. É
a prova de que a ordem entre as regras é requisito, não preferência.

**Alternativa rejeitada:** manter o rebaixamento do `det-2` (o vencido continuar na fila
como `possible`) em vez de suprimir. Os 18 casos julgados não sustentaram nenhum
candidato rebaixado, e mantê-los significa pedir trabalho humano para confirmar o que a
própria arbitragem já decidiu. Também foi rejeitado criar regra para os erros restantes:
os cinco falsos positivos que sobram não são problema de matching.

**Consequência:** o motor passou a errar menos e a pedir menos revisão — 103 → 47 pares
encaminhados. Em troca, suprimir é a primeira ação do motor que **remove** candidato, e
por isso vale só quando a especificidade ou a marca já identificaram outro produto na
mesma reclamação; qualquer regra futura de supressão precisa do mesmo tipo de evidência
rotulada antes de entrar.

Consequência não prevista, encontrada em revisão: como o par suprimido é datado como
obsoleto, ele sai da fila **mesmo quando o revisor o marcou `possible`** — a arbitragem
passa por cima da dúvida humana, contra o que D-010 e `docs/DOMAIN.md` afirmam. Três pares
ficaram nessa situação no primeiro reprocessamento. Está em aberto, registrado em
`docs/STATE.md`: reabrir os pares, mudar a regra de pendência ou assumir a precedência da
arbitragem e corrigir o domínio.

**Limites conhecidos, deliberadamente não tratados:**
- cinco falsos positivos restantes: quatro são reclamação de compra, entrega ou troca, não
  do aparelho (#43, #50, #75, #100 em parte) — classificação do assunto da reclamação, que
  não é matching e terá decisão própria; e dois (#97, #100) só existem porque "FreeStyle
  Libre 2" não está cadastrado, o que se resolve por cadastro;
- dois falsos negativos restantes: #2 ("Accu check guide me", com "check" em vez de "chek",
  cuja forma compactada não coincide) e #91 (o texto não nomeia modelo; o revisor decidiu
  pelo corpo). Corrigi-los exigiria heurística sob medida para dois casos;
- as métricas valem para o corpus que originou as regras. **Antes de tratar o `det-3` como
  estável, ele precisa ser validado em amostra independente**, coletada depois e rotulada
  inteira antes de qualquer novo ajuste.

---

## D-015 — Os rótulos humanos como conjunto permanente de regressão

**Decisão:** os 145 pares julgados ficam versionados em
`golden/glicosimetro-2026-09-25.json` (produtos, reclamações e o parecer de cada par) e
`tests/test_regressao_rotulada.py` mede o motor contra eles a cada execução da suíte. O
resultado do `det-2` está fixado no teste como âncora; o do `det-3` entra como piso.

**Motivo:** sem gabarito, "melhorou" é opinião. Com ele, uma regra nova é aceita ou
rejeitada por medida — e o custo de descobrir que ela removeu um acerto cai de uma rodada
de validação humana para uma execução de teste.

**Alternativa rejeitada:** manter os rótulos apenas no banco. O banco é estado vivo: uma
revisão posterior sobrescreve o parecer anterior (D-010), e o conjunto deixaria de ser
fixo justamente quando fosse mais necessário.

**Consequência:** o conjunto é fixo e não deve ser reescrito para acomodar mudança de
motor; corrigir um rótulo é ato deliberado, com justificativa. Ele também não substitui
validação em dados novos: mede regressão sobre o corpus que originou as regras, e por
construção não detecta o que esse corpus não contém.

---

## D-016 — O Golden Dataset versionado, com separação entre desenvolvimento e holdout

**Decisão:** validação humana vira gabarito em arquivo, não em consulta ao banco.
`app/golden.py` define o formato (cabeçalho com `dataset_id`, `split`, quem rotulou,
quando e origem; produtos, reclamações e o parecer de cada par), `app/golden_export.py`
congela um recorte do banco e `app/evaluation.py` mede uma versão do motor contra os
conjuntos de `golden/`, pela linha de comando ou pelo teste de regressão. `split` diz o
que a medida significa: `development` é o corpus que originou as regras e mede
**regressão**; só `holdout` mede desempenho. Fatia com menos de 30 pares decididos ou 5
positivos humanos reporta contagens e recusa precisão, recall e F1.

**Motivo:** D-015 congelou os rótulos, mas a medição vivia dentro de um teste. Sem formato
e sem `split`, o segundo conjunto nasceria com outro layout, e o número do corpus de
desenvolvimento continuaria sendo lido como desempenho — que é exatamente o erro que a
fase existia para impedir.

**Alternativa rejeitada:** medir direto do banco, sem arquivo. O banco acumula e é
reescrito pela revisão seguinte; a mesma pergunta daria respostas diferentes em datas
diferentes, e nenhuma seria reproduzível offline.

**Consequência:** a extração exclui todo par já congelado em outro conjunto — sem isso, o
banco devolveria os pares de desenvolvimento dentro do `holdout` e a contaminação passaria
despercebida. Gabarito não se reescreve: gravar por cima exige `overwrite=True`. O motor
passou a ter um caminho único (`match_complaint`), usado por produção e por medição, para
que as duas não divirjam. Limite assumido no formato: o rótulo existe para o par que
alguma versão do motor propôs, então o recall medido é recall sobre pares propostos —
ampliar isso exige rotular por reclamação, não por candidato.

---

## D-017 — Tendência exige cobertura de coleta, não só significância

**Decisão:** o dashboard calcula tudo em SQL (`app/analytics.py`). A comparação entre dois
períodos de igual duração termina sempre na **última coleta** — nunca em `date_to` ou "hoje",
porque dia posterior à última busca não tem reclamação no banco por construção e contá-lo
simula queda — e só recebe direção (`alta`/`queda`) quando passa **duas** condições
independentes, ambas devolvidas na resposta: `comparable` — cada janela teve busca própria,
contada em `search_run.started_at` — e `significant` — a diferença não é compatível com
oscilação, pelo teste exato condicional (Binomial(n, ½) sobre o total das duas janelas,
bilateral, α = 0,05, mínimo de 10 reclamações somadas). Faltando qualquer uma, a API devolve
os números, `direction: null` e a `note` dizendo qual condição faltou. A data de referência da
análise é a **publicação** da reclamação; reclamação sem data legível fica fora de toda série
e é contada à parte em `undated`.

**Motivo:** no banco real a janela de 90 dias tem 57 reclamações contra 1 da anterior —
p ≈ 0, significância folgada. Mas todas as 13 buscas aconteceram dentro da janela atual: a
coleta é dirigida por termo e a paginação está bloqueada (D-011), então o período recente é
sistematicamente mais coberto. Sem a checagem de cobertura, o dashboard anunciaria como
crescimento de reclamações o recorte da própria coleta — exatamente o tipo de afirmação que
uma análise de vigilância não pode fazer.

**Alternativa rejeitada:** exibir a variação percentual com uma ressalva em texto. A ressalva
é lida depois do número, quando ele já foi interpretado; e o percentual (+5.600%) carrega mais
autoridade do que qualquer nota ao pé. Também foi rejeitado pedir a leitura da tendência a uma
LLM: nada no dashboard é descrito por modelo, só contado.

**Consequência:** a janela devolvida pode ser anterior à pedida, e é a devolvida que vale —
foi assim que apareceu uma "queda" de 78% na janela de 7 dias, formada só pelos quatro dias
entre a última coleta e hoje. Enquanto houver uma coleta só, nenhuma tendência é afirmada — e isso é o
resultado correto, não uma limitação a contornar. Tendência passa a depender de coleta
periódica; quando ela existir, as duas janelas terão buscas e a condição se satisfaz sozinha.
O escopo de produto/categoria vale para todas as rotas do dashboard, e "reclamação de um
produto" é sempre a que tem correspondência **vigente e não descartada** — par obsoleto ou
descartado deixa de contar sem ser apagado, e a decisão humana prevalece na contagem como
prevalece no par (D-010).

## D-018 — Implantação só local, protegida por Host e origem, sem autenticação

**Decisão:** o MVP roda apenas na máquina do analista. Em vez de autenticação, a API recusa
com `400` todo `Host` fora de `RAPM_ALLOWED_HOSTS` (padrão `127.0.0.1`, `localhost`) e com
`403` todo `POST`/`PATCH` cujo `Origin` não seja a própria API. Pedido sem `Origin` (curl,
scripts, testes) passa. CORS continua desabilitado.

**Motivo:** escutar em `127.0.0.1` não basta. Qualquer página aberta no navegador consegue
enviar formulário para `localhost`, e `POST /products/{id}/deactivate`, que não tem corpo,
era executável assim. DNS rebinding também permitia que um domínio externo lesse e
escrevesse na API. A checagem de `Host` fecha o rebinding e o acesso pela rede; a de
`Origin` fecha o CSRF.

**Alternativa rejeitada:** autenticação por usuário e senha ou token. Para um único usuário
local, ela só acrescentaria credencial para guardar sem reduzir o risco real, que vem do
navegador e não de outro usuário.

**Consequência:** expor a API na rede exige primeiro implementar autenticação. Ampliar
`RAPM_ALLOWED_HOSTS` ou mudar `--host` sem ela desfaz a proteção. `RAPM_ALLOWED_HOSTS` é uma
lista e, por variável de ambiente, precisa ser escrita em JSON.

