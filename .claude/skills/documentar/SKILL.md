---
name: documentar
description: Consolida o estado do projeto ao fim de uma fase sem gerar documentação redundante.
disable-model-invocation: true
---

Documente somente mudanças duráveis da fase atual.

1. Leia `docs/STATE.md`.
2. Consulte `git diff --stat` e, se necessário, os diffs relevantes.
3. Atualize `docs/STATE.md` mantendo-o curto:
   - fase;
   - concluído;
   - em andamento;
   - próximo;
   - bloqueios;
   - última atualização.
4. Atualize `docs/ARCHITECTURE.md` apenas se a arquitetura mudou.
5. Atualize `docs/DOMAIN.md` apenas se a taxonomia/regra de domínio mudou.
6. Atualize `docs/DECISIONS.md` apenas para decisão arquitetural importante, registrando:
   - decisão;
   - motivo;
   - alternativa rejeitada relevante;
   - consequência.
7. Não copie diffs, logs ou descrição de cada arquivo.
8. Não crie novos documentos sem necessidade.

Ao final, informe somente quais documentos foram atualizados e por quê.
