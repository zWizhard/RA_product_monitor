# Guia de uso do Claude Code neste projeto

Este arquivo é para você, não precisa ser lido pelo Claude em toda sessão.

## 1. Instalação do pacote

Extraia/copiei o conteúdo deste pacote para a raiz do novo repositório.

A raiz deve conter:

```text
CLAUDE.md
.claude/
docs/
prompts/
```

Abra **a raiz do projeto** no VS Code e inicie Claude Code nessa pasta.

## 2. Verifique se a configuração carregou

No Claude Code:

```text
/context
```

Confirme que `CLAUDE.md` aparece em **Memory files**.

As regras em `.claude/rules/` são carregadas quando os arquivos correspondentes são acessados.

## 3. Auto Memory

O projeto define:

```json
"autoMemoryEnabled": false
```

O estado entre fases fica deliberadamente em `docs/STATE.md`, evitando memória automática crescente.

## 4. Fluxo recomendado por fase

Abra um novo chat/contexto para cada fase importante.

Exemplo:

```text
/fase prompts/fases/00_bootstrap_limpeza.md
```

Depois da implementação e dos testes:

```text
/revisar
```

Se estiver tudo correto:

```text
/documentar
```

Então faça commit e comece a próxima fase em um contexto novo.

## 5. Quando usar /clear ou novo chat

Use quando:
- uma fase terminou;
- o contexto ficou grande;
- mudou de assunto técnico;
- Claude começou a revisitar decisões já estabilizadas.

Antes de limpar o contexto, execute `/documentar` se houver estado relevante ainda não registrado.

## 6. Como economizar tokens

Evite prompts como:
"analise todo o projeto e faça X".

Prefira:
"corrija X. Localize apenas os arquivos envolvidos e rode os testes afetados."

Não cole logs gigantes. Salve em arquivo e indique o caminho, ou envie apenas o trecho do erro.

Não peça documentação após cada modificação. Documente no fim da fase.

## 7. Modelos

Não há modelo fixado nos arquivos para evitar envelhecimento da configuração.

Para implementação cotidiana, prefira o modelo Sonnet atual disponível no seu Claude Code.
Use modelos mais caros apenas quando a tarefa realmente exigir raciocínio arquitetural difícil.

## 8. Permissões

`.claude/settings.json` libera automaticamente apenas comandos seguros e frequentes, como `git status`, `git diff` e testes.

Operações Git potencialmente destrutivas/externas continuam pedindo confirmação.

Arquivos `.env` são bloqueados para leitura pelo Claude.

## 9. Regra prática de chats

Um chat = uma fase ou um problema coerente.

Não mantenha um único chat de desenvolvimento por meses.
Git + `STATE.md` são a continuidade do projeto.
