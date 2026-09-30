# Estado atual

Fase: manutenção (`continuidade.md`). O motor continua congelado em `det-3`.

## Concluído
- Fases 0 a 2b: base em `app/`, cadastro com taxonomia fechada, coleta pela resposta JSON
  do endpoint de busca (D-011).
- Fases 3 e 4: `product_complaint_match` com método, score, evidência e explicação por par;
  decisão humana separada da automática; reprocessamento idempotente; par sem evidência
  datado, nunca apagado; fila de revisão derivada.
- Fase 4b: 8 glicosímetros, 8 termos coletados (75 reclamações), motor `det-2` (D-012) e
  proveniência da decisão automática por versão (D-013).
- Fase 4c: os 145 pares foram rotulados um a um pelo revisor e viraram conjunto permanente
  de regressão (D-015); sobre eles o motor passou a `det-3` (D-014), com `det-2`
  preservado executável em `MatchingConfig`/`DET2`.
- Fase 5: o gabarito virou artefato versionado com formato próprio e `split`
  desenvolvimento/holdout, extraível do banco e medível offline (D-016).
- Fase 6: métricas contadas em SQL (`app/analytics.py`), quatro rotas em `/dashboard` com
  escopo comum e um dashboard sem framework servido em `/`. Tendência só é afirmada com
  cobertura de coleta **e** significância (D-017). Nada é descrito por LLM.
- Fase 7: a API só atende a máquina local. `Host` fora da lista recebe `400`, escrita
  vinda de outra origem recebe `403` (D-018). Instalação limpa num venv novo verificada,
  com 258 testes passando. Fluxo cadastro → reclamações → matching → revisão → dashboard
  testado com uvicorn sobre uma cópia do banco real. Nenhum segredo nos arquivos a versionar.
  A pasta legada `RA/` foi apagada pelo usuário, sem migração do banco antigo.
- Manutenção: interface em abas com pesquisa livre e Tutorial; na Auditoria, o item
  decidido sai da lista de pendentes e fica em "Avaliações já validadas"
  (`/matches/queue?scope=reviewed`). Dúvida humana virou decisão final (D-019).
  "Ver monitoramento" em Produtos cadastrados abre a página do produto, composta só de
  rotas existentes filtradas por `product_id` (sem rota nem tabela nova).
- Manutenção, dashboard: situação na fonte em português (o banco guarda o código),
  Pareto de produtos com % acumulado, triagem da ocorrência (`/dashboard/natures`, regra
  `rel-4`, indício por vocabulário) e qualidade do matching (automático × humano; precisão
  só com todo confirmado automático revisado).
- Manutenção, monitoramento periódico (D-020): `python -m app.monitor` coleta todos os
  produtos ativos, roda o matching das buscas feitas e grava relatório Markdown em
  `data/reports/`; cada execução fica em `monitor_run`. `monitorar.bat` serve ao
  Agendador de Tarefas. Validado em cópia do banco real com `--sem-coleta`; coleta real
  pela rotina ainda não exercitada.
- Manutenção, busca ativa: coleta por termo geral sem produto (já existia) e, em
  `/research`, triagem por natureza e filtro produto identificado / não identificado. O
  produto vem só do matching; sem par, a reclamação continua listada. Sem tabela nova.
- Schema na versão 8 no código; o banco real ainda está na 7 (a migração 8 aplica na
  próxima abertura e, a partir daí, não se edita). 286 testes, sem rede.

## Em andamento
Nada. O motor só volta a mudar depois de validação em amostra independente.

## Onde as coisas estão
- **Instalar e subir:** `README.md`.
- **Dashboard:** `http://127.0.0.1:8000/` (a própria API serve `frontend/index.html`).
  Rotas: `/dashboard/overview`, `/top-products`, `/timeline`, `/trend`, `/natures`, todas com
  `product_id`, `category`, `subcategory`, `date_from`, `date_to`. O recorte vale sobre a
  **publicação** da reclamação; em buscas, sobre a execução.
- **O que o banco real mostra hoje:** 75 reclamações (0 sem data), 54 com produto
  associado, 86 pares vigentes (34 confirmados, 22 possíveis, 30 descartados), 22
  pendentes (contagem anterior a D-019, que tirou a dúvida humana dos pendentes), 59
  obsoletos; mais citados: Accu-Chek SmartGuide (18), FreeStyle Libre 2 Plus
  (13), Sibionics GS1 (11). Série de 74 meses, concentrada em 08–09/2026.
- **Gabarito:** `golden/glicosimetro-2026-09-25.json`, `split: development` — 8 produtos,
  75 reclamações, 145 rótulos. Conjunto fixo: não se reescreve para acomodar mudança de
  motor, e `save` recusa sobrescrita sem `overwrite=True`.
- **Medir:** `python -m app.evaluation [--matcher det-2] [--baseline det-2] [--json]`.
  `det-3` sobre o conjunto: precisão 0,865 · recall 0,941 · F1 0,901 · 47 encaminhados
  (o `det-2` faz 0,750 / 0,882 / 0,811 com 103). Como o `split` é `development`, isso é
  **regressão**, não desempenho — o relatório diz isso em toda execução.
- **Congelar novo recorte:** `python -m app.golden_export --id ... --split holdout --por
  ... --origem ...`. Leva só o que ainda não está em nenhum conjunto de `golden/`.
- **Banco real:** 86 pares vigentes (39 `confirmed`, 47 `possible`), 59 suprimidos e
  datados com a regra que os tirou, 22 pendentes de revisão. Nenhum par apagado, nenhum
  rótulo humano alterado.
- **Auditoria do `det-2`:** as 145 decisões continuam recuperáveis — 59 na própria linha
  (obsoleta, com `matcher_version = det-2` e `stale_reason`) e 86 em
  `product_complaint_match_revision`.

## Próximo
1. Agendar `monitorar.bat` (README) com o servidor fechado no horário e conferir a
   primeira execução real em `monitor_run` e no relatório.
2. **Validar o `det-3` em amostra independente**: coletar termos novos, rotular a amostra
   inteira antes de qualquer ajuste, congelar como `holdout` e só então decidir sobre o
   motor. É o único caminho para ter número de desempenho.
3. Cadastrar as linhas que faltam (FreeStyle Libre 2 e 3, OneTouch Ultra): fecha dois dos
   cinco falsos positivos restantes sem tocar no motor.
4. Decidir sobre classificação **compra × aparelho** — quatro dos cinco falsos positivos
   restantes são reclamação de venda, entrega ou troca. Não é matching.

## Bloqueios
Nenhum impede trabalho. Pendências conhecidas:
- **não há tendência a exibir enquanto a coleta for um único episódio.** As 13 buscas caem
  todas na janela atual, então `/dashboard/trend` devolve os números, `comparable: false` e
  nenhuma direção — 57 contra 1 é o recorte da coleta, não crescimento observado (D-017).
  Só coleta periódica resolve isso — a rotina existe (D-020), falta agendá-la e acumular
  semanas; não é caso de afrouxar a regra. A janela termina na
  última coleta (21/09/2026), nunca em "hoje": os dias sem coleta simulavam queda;
- `granularity=day` sem recorte devolve um passo por dia entre a primeira e a última
  publicação (≈2.200 hoje). Não trunca nem agrega: quem quiser dia usa `date_from`;
- **não existe `holdout`.** Toda métrica publicada mede regressão sobre o corpus que
  originou as regras; por construção ela não revela o que esse corpus não contém;
- o gabarito rotula o par que alguma versão do motor propôs, então o recall é recall sobre
  pares propostos — não sobre todos os pares possíveis (D-016);
- `app/golden_export.py` só foi exercitado contra banco de teste; ainda não houve recorte
  novo a congelar do banco real;
- a precedência por especificidade compara nomes, **não exige a mesma marca**. Hoje nenhum
  par cruza marcas, porque nenhum nome do catálogo está contido no de outra marca; com o
  catálogo maior, dois produtos de marcas distintas podem entrar nessa relação — e agora a
  consequência é supressão, não rebaixamento;
- o caminho "sem evidência" de `stale_reason` não tem teste; só o de supressão tem;
- falsos positivos conhecidos e não tratados: #43, #50, #75 (compra/entrega), #97 e #100
  ("Libre 2" fora do catálogo). Falsos negativos: #2 (grafia "check"/"chek") e #91 (texto
  sem modelo). Nenhum deve virar heurística sob medida;
- revisão humana guarda só a última decisão de cada par (D-010); a decisão automática tem
  histórico desde o `det-2`;
- `POST /matches/run` é síncrono: ~11 ms por par (≈9 ms do fuzzy). Fatiar por
  `limit`/`offset`; fatiar por `product_id` reduz o que é gravado, não o custo;
- **migração aplicada não se edita**: acrescentar comando a uma migração já registrada em
  `schema_meta` deixa o schema parcialmente migrado — aconteceu entre as versões 6 e 7 e
  derrubou um reprocessamento com erro de coluna inexistente. `_migrate` também não é
  atômico;
- a busca ativa é manual: a rotina periódica coleta só pelos termos dos produtos e o
  relatório semanal só conta reclamações com produto associado — as sem produto, mesmo
  com indício de evento adverso, só aparecem na aba Reclamações. Triagem humana dessas
  reclamações não é gravada;
- a rotina periódica não roda com o servidor aberto (DuckDB: um processo gravando); sai
  com código 2 e só o log (`data/logs/monitor.log`) registra, não `monitor_run`;
- apenas Python 3.14 na máquina;
- a coleta real (Playwright + Reclame Aqui) não foi exercitada na verificação da fase 7;
  os testes usam coletor falso. Em uso real (30/09/2026) ela funcionou;
- no Windows, `pip install` falha em caminho longo sem *long paths* habilitado;
- os 2 avisos da suíte vêm de Starlette/anyio, não do projeto;
- nome sem caracteres latinos gera chave natural vazia e pode ser rejeitado como duplicado;
- coleta acoplada ao endpoint interno de busca (D-011); paginação não funciona porque o
  bloqueio é da segunda navegação da mesma sessão — ampliar cobertura é mais termos.
  Reconfirmado em 30/09/2026 ("accu-chek", 10 páginas → 10 itens, 403 na página 2). A aba
  de coleta agora abre com 1 página e avisa desse limite;
- reclamação antiga costuma vir só com a prévia de 130 caracteres;
- **dashboard sem heatmap, reincidência nem radar de anomalias**: a fonte não traz tipo de
  problema e nada o grava (exigiria rótulo humano); a triagem é indício, não sustenta
  heatmap nem reincidência. O radar exigiria série histórica com coleta regular por
  produto — hoje concentrada em 08–09/2026 e dirigida por termo — e um método por produto
  no molde de `/dashboard/trend`. Recall/F1 não saem do banco: par não proposto não é
  gravado.

Última atualização: 2026-09-30 (manutenção)
