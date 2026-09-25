# Fase 1 — Modelo e cadastro de produtos

## Objetivo

Criar a base estruturada dos produtos que serão monitorados.

## Requisitos

Produto deve suportar, conforme aplicável:
- nome;
- marca;
- fabricante;
- categoria;
- subcategoria;
- modelo;
- registro/identificador regulatório opcional;
- aliases/sinônimos;
- termos de busca;
- ativo/inativo.

Categorias iniciais estão em `docs/DOMAIN.md`.

## Trabalho

1. Desenhe o schema mínimo.
2. Implemente persistência e migração/inicialização segura.
3. Implemente CRUD necessário para uso interno/API.
4. Separe aliases/termos quando isso melhorar busca e normalização.
5. Valide duplicidade e campos essenciais.
6. Crie testes determinísticos.

## Fora de escopo

- scraping;
- fuzzy matching;
- LLM;
- dashboard analítico.

## Aceite

É possível cadastrar, consultar, editar e desativar produtos sem perder rastreabilidade.

Ao concluir, `/revisar` e `/documentar`.
