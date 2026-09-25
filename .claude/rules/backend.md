---
paths:
  - "backend/**/*.py"
---

# Backend

- Use type hints nas funções públicas e interfaces relevantes.
- Valide entrada de API com Pydantic.
- Separe API, regras de negócio, acesso a dados, scraping e matching.
- Não use `except Exception: pass`.
- Não introduza dependência sem necessidade comprovada.
- Não replique regra de negócio no endpoint se ela pertence a service/domain.
- Preserve compatibilidade apenas quando ela fizer parte do escopo atual.
- Após alterar backend, execute os testes diretamente relacionados.
