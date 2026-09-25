---
paths:
  - "backend/**/database*.py"
  - "backend/**/repository*.py"
  - "backend/**/repositories/**/*.py"
  - "backend/**/models/**/*.py"
  - "backend/**/migrations/**/*"
---

# Banco e persistência

- Alterações de schema devem ser explícitas e justificadas.
- Preserve dados existentes; migrações destrutivas exigem autorização.
- Use chaves/constraints para integridade e deduplicação quando apropriado.
- Não conte inserção como sucesso quando `ON CONFLICT ... DO NOTHING` não inserir linha.
- Não esconda erro de banco silenciosamente.
- Datas persistidas devem ter semântica clara: origem, coleta, criação e atualização não são equivalentes.
- Relações `product <-> complaint` devem preservar método, score e evidência.
