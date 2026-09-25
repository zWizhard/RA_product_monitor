# Fase 6 — Dashboard e análise temporal

## Objetivo

Apresentar operação e sinais dos produtos monitorados usando métricas calculadas em código.

## Trabalho

Criar visualizações úteis para:
- produtos monitorados;
- reclamações coletadas;
- matches por status;
- pendências humanas;
- produtos mais citados;
- problemas/categorias, quando estruturados;
- evolução temporal;
- variação contra período anterior quando estatisticamente válida.

## Regras

- Backend calcula métricas.
- LLM, se usada, apenas interpreta dados já calculados.
- Não pedir à LLM tendências que não foram fornecidas numericamente.
- Não exibir campo que a API não retorna.
- Preserve filtros por produto/categoria/período.

## Aceite

Dashboard reflete dados reais do banco e toda tendência exibida é derivável das métricas estruturadas.

Ao concluir, `/revisar` e `/documentar`.
