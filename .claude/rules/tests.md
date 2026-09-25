---
paths:
  - "tests/**/*.py"
  - "backend/tests/**/*.py"
---

# Testes

- Teste comportamento público, não detalhes internos frágeis.
- Prefira fixtures pequenas e determinísticas.
- Não dependa da internet, Reclame Aqui ou API de LLM em testes unitários.
- Mock/fake integrações externas na camada correta.
- Para correção de bug, adicione teste de regressão quando viável.
- Rode primeiro os testes afetados; suíte completa apenas quando fizer sentido.
