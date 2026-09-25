---
name: revisar
description: Revisa somente as alterações atuais procurando bugs, regressões, riscos e testes faltantes.
disable-model-invocation: true
---

Revise as alterações atuais sem reanalisar o projeto inteiro.

1. Execute `git status --short`.
2. Execute `git diff --stat`.
3. Abra/diff apenas os arquivos alterados relevantes.
4. Verifique, nesta ordem:
   - bug funcional;
   - perda/corrupção de dados;
   - contrato API/banco/frontend incompatível;
   - segurança;
   - regressão;
   - teste faltante;
   - mudança fora do escopo.
5. Não proponha refatoração cosmética.
6. Não altere código automaticamente, salvo se `$ARGUMENTS` pedir correção.
7. Se não houver problema material, diga isso claramente.

Saída curta, ordenada por severidade, com arquivo/trecho quando aplicável.
