# CONTINUIDADE — RA Product Monitor

## Objetivo atual

O sistema já concluiu as fases previstas de desenvolvimento e validação.

Daqui para frente, o objetivo é apenas:

> manter o sistema funcional, estável e suficiente para o uso real.

Não buscar arquitetura ideal, novas funcionalidades ou melhorias sem necessidade concreta.

---

## Regra principal

Antes de qualquer alteração, perguntar:

> Isso é necessário para o sistema funcionar?

Se não for, não implementar.

Prioridade:

```text
funcionar > simplicidade > estabilidade > melhorias
```

---

## Economia de tokens

Usar sempre o mínimo de contexto necessário.

Preferir:

* busca por função/arquivo específico;
* leitura de pequenos trechos;
* `grep`/busca textual;
* diffs pequenos;
* respostas curtas.

Evitar:

* ler o repositório inteiro;
* reler documentação já conhecida;
* imprimir arquivos completos;
* auditorias gerais não solicitadas;
* explicações extensas;
* documentação redundante.

Consultar `docs/STATE.md` e `docs/DECISIONS.md` somente quando forem relevantes para a tarefa.

---

## Alterações

Fazer somente quando houver:

* bug reproduzível;
* falha no fluxo principal;
* problema real observado no uso;
* necessidade para instalação/execução;
* risco de integridade ou segurança.

Não alterar por:

* preferência arquitetural;
* refatoração estética;
* otimização teórica;
* modernização;
* nova biblioteca;
* possibilidade de deixar o código “mais profissional”.

Sempre preferir:

```text
patch pequeno > refatoração
```

---

## Matching

Preservar o comportamento já validado.

Um caso isolado não justifica nova regra.

Quando houver erro real:

```text
registrar → acumular evidência → avaliar → decidir
```

Não criar `det-4` sem necessidade comprovada.

---

## Fluxo mínimo

O sistema precisa continuar permitindo:

```text
cadastrar produto
→ coletar reclamações
→ executar matching
→ visualizar candidatos
→ validar manualmente
→ consultar/exportar resultados
```

A validação humana faz parte do sistema e não precisa ser eliminada.

---

## Testes

Após alterações:

* executar testes diretamente relacionados;
* rodar suíte completa somente quando houver impacto amplo;
* criar teste novo apenas para comportamento ou regressão relevante.

---

## Regra de parada

Quando a tarefa solicitada estiver funcionando:

> PARE.

Não procurar novas melhorias, refatorações ou problemas fora do escopo.

---

## Resposta do Claude

Ao finalizar:

```markdown
## Feito
- alteração realizada
- testes executados

## Situação
Funcionando / pendência real.

## Próximo passo
Somente se necessário.
```

Sem relatório longo.

---

## Instrução permanente

Para cada nova tarefa:

```text
1. entender o problema;
2. buscar somente o contexto necessário;
3. alterar o mínimo possível;
4. preservar comportamento validado;
5. testar;
6. responder de forma curta;
7. parar quando funcionar.
```

> Não transformar este projeto em algo maior do que ele precisa ser.
