---
name: fase
description: Executa uma fase de desenvolvimento com escopo controlado e mínimo uso de contexto.
disable-model-invocation: true
---

Execute a fase indicada em `$ARGUMENTS`.

1. Leia `CLAUDE.md`.
2. Leia `docs/STATE.md`.
3. Se `$ARGUMENTS` indicar um arquivo em `prompts/fases/`, leia somente esse arquivo.
4. Localize o código necessário com busca antes de abrir arquivos grandes.
5. Confirme internamente: objetivo, fora de escopo, critérios de aceite e testes.
6. Implemente apenas o necessário para a fase.
7. Não implemente fases seguintes.
8. Rode primeiro os testes diretamente afetados.
9. Não atualize documentação extensa durante a implementação.
10. Ao finalizar, responda em até 10 linhas com:
   - resultado;
   - arquivos alterados;
   - testes executados;
   - bloqueios/pendências reais.

Se a fase estiver concluída e houver mudança relevante de estado, sugira executar `/documentar`.
