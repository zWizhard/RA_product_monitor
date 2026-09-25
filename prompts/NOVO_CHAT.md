# Prompt mínimo para um novo chat

Use este modelo quando não quiser chamar `/fase` diretamente.

```text
Leia CLAUDE.md e docs/STATE.md.

Trabalhe apenas na tarefa abaixo.
Localize somente os arquivos necessários antes de editar.
Não implemente funcionalidades futuras.
Rode os testes diretamente afetados.
Ao final, responda de forma curta com: alterações, testes e bloqueios.

Tarefa:
[DESCREVA AQUI]
```

Para uma fase pronta, prefira:

```text
/fase prompts/fases/XX_nome_da_fase.md
```
