# Pacote Claude Code — RA Product Monitor

Conteúdo pronto para copiar para a raiz do projeto.

## Incluído

- `CLAUDE.md`: instruções persistentes e enxutas.
- `.claude/settings.json`: Auto Memory desligada + permissões seguras.
- `.claude/rules/`: regras condicionais por tipo de arquivo.
- `.claude/skills/fase`: `/fase`.
- `.claude/skills/revisar`: `/revisar`.
- `.claude/skills/documentar`: `/documentar`.
- `docs/`: estado, arquitetura, domínio e decisões.
- `prompts/fases/`: prompts das fases 0–7.
- `prompts/`: modelos curtos para bug, feature e novo chat.
- `.gitignore`: venv, segredos, bancos locais e configuração pessoal.
- `CLAUDE_CODE_GUIDE.md`: como usar o ambiente.

## Primeiro uso

1. Copie o conteúdo para a raiz do novo repositório.
2. Abra a raiz no VS Code.
3. Inicie Claude Code.
4. Rode `/context` e confirme `CLAUDE.md`.
5. Comece com:

```text
/fase prompts/fases/00_bootstrap_limpeza.md
```

Ao terminar:

```text
/revisar
/documentar
```

Faça commit e abra um contexto novo para a próxima fase.
