# Fase 5 — Golden Dataset e avaliação

## Objetivo

Transformar validações humanas confiáveis em conjunto de referência para medir o motor de matching.

## Trabalho

1. Definir formato versionável do Golden Dataset.
2. Separar dados de treino/configuração de dados de avaliação, se aplicável.
3. Medir no mínimo:
   - precision;
   - recall;
   - F1;
   - falsos positivos;
   - falsos negativos.
4. Reportar métricas globalmente e por categoria quando houver amostra suficiente.
5. Criar gate/regressão somente após existir baseline confiável.
6. Não maquiar ausência de amostra como métrica conclusiva.
7. Garantir execução offline/reproduzível.

## Aceite

Mudanças no matching podem ser comparadas objetivamente contra um conjunto humano versionado.

Ao concluir, `/revisar` e `/documentar`.
