# Fase 4 — Validação humana

## Objetivo

Permitir revisão humana dos matches sem destruir a decisão automática original.

## Trabalho

1. Criar estados claros: confirmado, possível/pendente e descartado.
2. Preservar separadamente:
   - decisão automática;
   - decisão humana.
3. Registrar data e identificador do validador quando disponível.
4. Criar fila/filtro de pendências.
5. Permitir visualizar reclamação, produto candidato, score, método e evidência.
6. Evitar edição acidental do texto/fonte original.
7. Criar testes de transição de estado e persistência.

## Fora de escopo

- métricas avançadas;
- treino/modelo próprio;
- automação de decisão humana.

## Aceite

Um analista consegue confirmar/descartar matches e o histórico automático permanece auditável.

Ao concluir, `/revisar` e `/documentar`.
