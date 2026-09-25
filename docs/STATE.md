# Estado atual

Fase: 7 — Segurança e estabilização concluídas. O motor continua congelado em `det-3`.

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
- Schema na versão 7. 258 testes passando, sem rede.

## Em andamento
Nada. O motor só volta a mudar depois de validação em amostra independente.

## Onde as coisas estão
- **Instalar e subir:** `README.md`.
- **Dashboard:** `http://127.0.0.1:8000/` (a própria API serve `frontend/index.html`).
  Rotas: `/dashboard/overview`, `/top-products`, `/timeline`, `/trend`, todas com
  `product_id`, `category`, `subcategory`, `date_from`, `date_to`. O recorte vale sobre a
  **publicação** da reclamação; em buscas, sobre a execução.
- **O que o banco real mostra hoje:** 75 reclamações (0 sem data), 54 com produto
  associado, 86 pares vigentes (34 confirmados, 22 possíveis, 30 descartados), 22
  pendentes, 59 obsoletos; mais citados: Accu-Chek SmartGuide (18), FreeStyle Libre 2 Plus
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
0. **Decidir o que fazer com a dúvida humana em par suprimido** (ver bloqueios): reabrir os
   três pares, mudar a regra de pendência, ou assumir que a arbitragem vence a dúvida e
   registrar isso em `docs/DOMAIN.md`. Enquanto não houver decisão, o domínio e o código
   discordam.
1. **Validar o `det-3` em amostra independente**: coletar termos novos, rotular a amostra
   inteira antes de qualquer ajuste, congelar como `holdout` e só então decidir sobre o
   motor. É o único caminho para ter número de desempenho.
2. Cadastrar as linhas que faltam (FreeStyle Libre 2 e 3, OneTouch Ultra): fecha dois dos
   cinco falsos positivos restantes sem tocar no motor.
3. Decidir sobre classificação **compra × aparelho** — quatro dos cinco falsos positivos
   restantes são reclamação de venda, entrega ou troca. Não é matching.
4. Versionar o projeto: `git init` local, `origin` em
   `github.com/zWizhard/RA_product_monitor`, primeiro commit. Antes do push, conferir se
   `golden/*.json` pode ser publicado, porque não está no `.gitignore`.

## Bloqueios
Nenhum impede trabalho. Pendências conhecidas:
- **não há tendência a exibir enquanto a coleta for um único episódio.** As 13 buscas caem
  todas na janela atual, então `/dashboard/trend` devolve os números, `comparable: false` e
  nenhuma direção — 57 contra 1 é o recorte da coleta, não crescimento observado (D-017).
  Só coleta periódica resolve isso; não é caso de afrouxar a regra. A janela termina na
  última coleta (21/09/2026), nunca em "hoje": os dias sem coleta simulavam queda;
- o dashboard não tem categoria de problema: a fonte não entrega isso estruturado. O que
  existe é o `status` do Reclame Aqui (ANSWERED/PENDING), exibido como tal. Classificar
  motivo de reclamação seria trabalho de outra fase, com rótulo humano;
- `granularity=day` sem recorte devolve um passo por dia entre a primeira e a última
  publicação (≈2.200 hoje). Não trunca nem agrega: quem quiser dia usa `date_from`;
- **a supressão vence a dúvida humana.** Par suprimido é datado como obsoleto e sai da
  fila mesmo quando o revisor o marcou `possible` — o que contraria "quando existe decisão
  humana, é ela que vale" (`docs/DOMAIN.md`, D-010). Aconteceu com 3 pares no
  reprocessamento do `det-3` (#101, #126, #142): o rótulo e a evidência continuam
  gravados, o que se perdeu foi a pendência. Nenhum par **confirmado** pelo revisor foi
  suprimido, mas isso é propriedade dos dados, não garantia do código;
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
- o projeto ainda não é repositório Git local (o remoto já existe); apenas Python 3.14 na
  máquina. Sem histórico, o que se apaga não volta — daí a recusa de sobrescrever gabarito;
- a coleta real (Playwright + Reclame Aqui) não foi exercitada na verificação da fase 7;
  os testes usam coletor falso;
- no Windows, `pip install` falha em caminho longo sem *long paths* habilitado;
- os 2 avisos da suíte vêm de Starlette/anyio, não do projeto;
- nome sem caracteres latinos gera chave natural vazia e pode ser rejeitado como duplicado;
- coleta acoplada ao endpoint interno de busca (D-011); paginação não funciona porque o
  bloqueio é da segunda navegação da mesma sessão — ampliar cobertura é mais termos;
- reclamação antiga costuma vir só com a prévia de 130 caracteres.

Última atualização: 2026-09-25 (fase 7)
