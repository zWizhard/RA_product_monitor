---
paths:
  - "frontend/**/*.{html,css,js,jsx,ts,tsx}"
---

# Frontend

- Mantenha a interface simples até o backend e o modelo de dados estabilizarem.
- Não introduza framework ou biblioteca nova sem necessidade comprovada.
- Escape/sanitize conteúdo externo antes de inserir em HTML.
- Não replique regras de matching/classificação do backend no frontend.
- Estados de loading, erro e vazio devem ser explícitos.
- Campos exibidos devem existir no contrato real da API.
