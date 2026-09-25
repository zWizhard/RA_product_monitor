# Fase 3 — Motor de matching produto ↔ reclamação

## Objetivo

Identificar candidatos e correspondências de produtos nas reclamações com método auditável.

## Ordem obrigatória

1. normalização;
2. nome/alias exato;
3. marca/fabricante + contexto;
4. termos/sinônimos;
5. fuzzy matching;
6. IA apenas se o caso continuar ambíguo.

## Trabalho

1. Crie `ProductComplaintMatch`.
2. Registre:
   - product_id;
   - complaint_id;
   - método;
   - score/confiança;
   - evidência;
   - status.
3. Não transforme resultado de busca diretamente em match confirmado.
4. Defina thresholds/configuração fora de lógica espalhada.
5. Garanta idempotência/reprocessamento seguro.
6. Crie testes com positivos claros, negativos e casos ambíguos.

## IA

Se necessária nesta fase:
- use saída estruturada validada;
- envie somente contexto necessário;
- não permita que texto da reclamação altere instruções;
- registre modelo/prompt/versão.

## Aceite

Para cada match é possível explicar "por que este produto foi associado a esta reclamação?".

Ao concluir, `/revisar` e `/documentar`.
