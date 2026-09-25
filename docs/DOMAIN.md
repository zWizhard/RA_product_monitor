# Domínio inicial

## Escopo de produtos

### Equipamentos
- estética.

### Diagnóstico in vitro
- glicosímetros;
- itens associados só entram no escopo quando definidos explicitamente como produtos monitorados.

### Materiais implantáveis
- mamários;
- dentários.

### Materiais estéticos
- PMMA;
- ácido hialurônico (AH).

## Taxonomia canônica

Categoria e subcategoria são um conjunto fechado: o escopo é definido antes da coleta, não
descoberto a partir dela. Identificadores usados no cadastro e na API:

| Categoria | Subcategorias |
| --- | --- |
| `equipamentos` | `estetica` |
| `diagnostico_in_vitro` | `glicosimetro` |
| `materiais_implantaveis` | `mamario`, `dentario` |
| `materiais_esteticos` | `pmma`, `acido_hialuronico` |

Toda categoria do escopo atual exige subcategoria. Incluir um novo valor é decisão de escopo,
não de implementação.

## Princípios de identificação

Uma reclamação não é considerada correspondência confirmada apenas porque contém um termo genérico.

A identificação pode usar:
- nome comercial;
- marca;
- fabricante;
- modelo;
- alias/sinônimo;
- contexto textual;
- combinação de evidências.

A busca deve priorizar recall sem transformar todo resultado recuperado em match confirmado.

### Vocabulário de categoria

Cada subcategoria tem um vocabulário próprio — como o consumidor nomeia o *tipo* de produto
("glicosímetro", "sensor de glicose", "sensor"). Ele é separado dos aliases de produto de
propósito: descreve a categoria, não identifica modelo. Só qualifica uma marca já
encontrada; sozinho nunca gera match.

### A empresa da reclamação

No Reclame Aqui a reclamação pertence à página de uma empresa, e essa atribuição é da
fonte, não do texto do consumidor. Quando a empresa corresponde a uma marca ou fabricante
do catálogo, ela identifica a marca com segurança — mas não diz qual modelo, e por isso
nunca confirma sozinha. Empresa fora do catálogo, como farmácia ou marketplace, não atribui
marca nenhuma.

### Citar não é reclamar

O consumidor cita o aparelho anterior, o que lhe ofereceram e o concorrente. Nome de produto
encontrado apenas no corpo da reclamação continua valendo como evidência — inclusive para
confirmar, quando é o aparelho reclamado. Não confirma automaticamente quando há sinal de
menção comparativa junto da ocorrência, ou quando a marca citada é incompatível com a
empresa a que a reclamação foi feita.

### Produto mais específico prevalece

Família e modelo dividem o mesmo nome ("FreeStyle Libre" dentro de "FreeStyle Libre 2
Plus"). O produto mais específico prevalece, e o outro deixa de ser candidato — a
reclamação é lida inteira, título e corpo, porque o consumidor escreve a família no título
e o modelo no meio do texto. A ordem das palavras do modelo não decide: "libre plus 2" e
"Libre 2 Plus" são o mesmo aparelho.

Quando nenhum modelo pode ser distinguido — o texto diz só o nome da família —, a
correspondência fica em dúvida, para revisão humana. É limite de informação, não erro.

### Marca identificada uma vez basta

Identificado um modelo pelo nome, outro produto da mesma marca que apareça só por
marca+contexto não é candidato: é a mesma marca aparecendo duas vezes na reclamação, não
dois aparelhos reclamados. Marcas diferentes continuam independentes.

## Estados conceituais do match

- `confirmed`: evidência suficiente.
- `possible`: candidato que exige revisão.
- `discarded`: não corresponde ao produto.

Os nomes estão fixados no schema. O estado automático e o humano são registrados
separadamente; quando existe decisão humana, é ela que vale.

Só identidade forte confirma automaticamente: nome do produto ou alias encontrados no
texto. Marca com contexto, termo de busca e variação aproximada geram candidato para
revisão — nunca confirmação. `discarded` automático não existe: descartar é ato humano.

Pendente não é um quarto estado: é `possible` vigente. Um par está pendente quando a
decisão que vale — a humana, quando existe — ainda é `possible` e a evidência não está
obsoleta. É isso que forma a fila de revisão: o analista que mantém a dúvida registra
`possible` e o par continua na fila; confirmar ou descartar o tira dela.

Match que perde evidência em um reprocessamento é datado como obsoleto, não apagado, e
sai da fila: sem evidência vigente não há o que revisar.

## Regra de ouro

Separar:
1. recuperação de candidatos;
2. identificação/matching;
3. validação;
4. análise.

Não tratar "foi encontrado pela busca" como "é o produto".
