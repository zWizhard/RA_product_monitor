# Fase 2 — Busca e coleta no Reclame Aqui

## Objetivo

Executar buscas derivadas de produtos/termos e preservar reclamações encontradas com proveniência real.

## Trabalho

1. Reutilize somente o scraper útil da base RA.
2. Separe `Search` de `Complaint`.
3. Registre qual busca/termo encontrou cada reclamação.
4. Preserve URL/ID original, conteúdo e timestamps disponíveis.
5. Diferencie contagens:
   - encontrados;
   - coletados;
   - novos inseridos;
   - duplicados;
   - falhas.
6. Deduplicate por identificador/URL real; não invente URL.
7. Trate erro, timeout e mudanças de seletor de forma observável.
8. Crie testes sem acesso real à internet para parser/persistência.

## Fora de escopo

- confirmar produto automaticamente;
- LLM;
- dashboard final.

## Aceite

Uma execução de busca produz reclamações rastreáveis e métricas de coleta corretas.

Ao concluir, `/revisar` e `/documentar`.
