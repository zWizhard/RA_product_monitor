# Fase 7 — Segurança e estabilização

## Objetivo

Preparar o MVP para uso confiável no ambiente definido.

## Trabalho

1. Revisar contratos frontend/API/banco.
2. Remover código morto e arquivos legados restantes.
3. Revisar:
   - CORS;
   - binding de rede;
   - autenticação, se houver acesso além da máquina local;
   - sanitização/XSS;
   - TLS;
   - segredos;
   - prompt injection;
   - exclusão/alteração de dados.
4. Rodar suíte completa.
5. Corrigir warnings relevantes.
6. Verificar instalação limpa em ambiente novo.
7. Documentar comando de instalação e execução.

## Aceite

- suíte passa;
- projeto instala e inicia em ambiente limpo;
- não há segredo versionado;
- operações críticas têm proteção adequada ao modo de implantação;
- fluxo principal produto → busca → match → validação funciona ponta a ponta.

Ao concluir, `/revisar` e `/documentar`.
