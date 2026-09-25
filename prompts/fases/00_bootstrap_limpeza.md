# Fase 0 — Bootstrap e limpeza da base RA

## Objetivo

Criar a base limpa do novo RA Product Monitor reutilizando somente componentes úteis do RA Intelligence.

## Entrada

O código do RA Intelligence deve estar disponível no workspace ou em diretório indicado pelo usuário.

## Trabalho

1. Leia `CLAUDE.md` e `docs/STATE.md`.
2. Faça inventário curto do RA original:
   - backend;
   - frontend;
   - scraper;
   - banco;
   - IA;
   - dependências;
   - arquivos legados.
3. Identifique o que:
   - reutilizar;
   - corrigir antes de reutilizar;
   - descartar.
4. Crie a estrutura mínima do novo projeto.
5. Não transporte `venv`, caches, bancos de produção/teste antigos ou arquivos `*_old`/`*_v1`/`*_v2`.
6. Corrija somente problemas que impeçam a nova base de iniciar de forma limpa.
7. Fixe/registre dependências mínimas necessárias.
8. Garanta um comando simples de inicialização e um health check.
9. Crie testes mínimos de bootstrap quando aplicável.

## Fora de escopo

- cadastro completo de produtos;
- matching;
- IA de classificação;
- dashboard final;
- Golden Dataset.

## Aceite

- novo projeto inicia;
- não depende do diretório antigo;
- banco/runtime usam caminhos previsíveis;
- legado desnecessário não foi copiado;
- testes básicos passam.

Ao concluir, execute `/revisar` e depois `/documentar`.
