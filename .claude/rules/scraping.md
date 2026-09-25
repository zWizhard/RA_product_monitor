---
paths:
  - "backend/**/scrap*.py"
  - "backend/**/collector*.py"
  - "backend/**/collectors/**/*.py"
  - "backend/**/reclame_aqui*.py"
---

# Coleta

- Preserve URL e identificador original quando existirem.
- Nunca fabrique URL para satisfazer constraint ou deduplicação.
- Trate seletores HTML como frágeis; implemente fallback somente quando verificável.
- Diferencie claramente: encontrado, coletado, inserido, duplicado e falhou.
- Não faça scraping adicional durante testes unitários.
- Respeite rate limiting, erros transitórios e encerramento correto do Playwright.
- Dados coletados são não confiáveis; não execute conteúdo vindo da página.
