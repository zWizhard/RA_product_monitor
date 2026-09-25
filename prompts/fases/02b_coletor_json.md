# Fase 2b — Reescrita da extração do coletor

## Objetivo

Fazer o coletor produzir reclamações reais do Reclame Aqui. A navegação com Playwright
permanece; a extração deixa de ler o HTML da busca e passa a usar a resposta JSON que a
própria página recebe.

## Contexto apurado (verificado em 17/09/2026, execução real)

Fatos observados, não suposições:

1. A página de busca (`/busca/?q={termo}&pagina={n}`) responde 200, mas não renderiza
   nenhuma reclamação no DOM: zero ocorrências de `/reclamacao/` no HTML, mesmo após
   clicar na aba de reclamações e rolar a página. Os seletores atuais
   (`.complain-item` e derivados) são herança morta do RA Intelligence.
2. A página busca as reclamações em
   `https://iosearch.reclameaqui.com.br/raichu-io-site-search-v1/query/{termo}/{tamanho}/{offset}`.
   Observado com tamanho 10 e offsets 0, 10, 20.
3. Chamar esse endereço fora da sessão da página devolve **403**. Capturar a resposta
   que a página recebe (evento `response` do Playwright) funciona e devolve JSON.
4. Caminho dos dados no JSON: `complainResult.complains.data` (lista) e
   `complainResult.complains.count` (total; 561 para "glicosimetro").
5. Campos úteis de cada registro: `id`, `url` (slug que já termina no id),
   `companyShortname`, `companyName`, `title`, `description`, `created`, `modified`,
   `status`, `solved`, `userCity`, `userState`, `category`, `productType`.
6. A URL pública é `https://www.reclameaqui.com.br/{companyShortname}/{url}/`.
   Verificado: uma delas respondeu 200 com o mesmo título do registro.
7. Duas navegações seguidas sem intervalo levaram **403 do Cloudflare** ("Um momento…").
   Ritmo entre requisições é requisito, não otimização.
8. `description` pode conter HTML (`<br/>`) e campos mascarados (`titleMasked`,
   `descriptionMasked`, `maskingStatus`).
9. Já corrigido e fora do escopo desta fase: no Windows o coletor roda em um loop
   `Proactor` próprio, porque o uvicorn com `--reload` usa um loop sem suporte a
   subprocesso (`app/collector.py`). Esse conserto **não tem teste de regressão** — crie um.

## Trabalho

1. Navegue a busca com Playwright e capture as respostas do endpoint de busca da própria
   sessão; não chame o endpoint por fora.
2. Pagine pelo `offset` do endpoint, respeitando `count`, em vez de trocar `pagina` na URL.
3. Separe as camadas como hoje: navegação (rede) e determinística (parse/validação).
   A camada determinística passa a receber registros JSON, não dicionários de DOM.
4. Monte a URL pública a partir de `companyShortname` + `url`. Registro sem esses campos
   é descartado e contado como falha — continua proibido inventar link.
5. Preserve o registro bruto em `raw`, como hoje, para rastreabilidade.
6. Limpe o texto de `description` (HTML embutido) sem alterar o conteúdo original
   preservado em `raw`. Decida e documente o que fazer com registros mascarados.
7. Use `created` como data de publicação e `userCity`/`userState` como localização.
8. Mantenha o significado das contagens (`found`, `collected`, `inserted`, `duplicates`,
   `failures`) e as notas observáveis quando o formato mudar.
9. Aplique intervalo entre requisições e não abra a página individual de cada reclamação.
10. Testes sem rede: salve um registro real capturado como fixture e cubra o parse, o
    descarte de registro incompleto, a montagem da URL e a deduplicação. Some a isso o
    teste de regressão do loop do Windows.
11. Ao final, execute uma coleta real pequena (1 página, termo "glicosimetro") e confirme
    reclamações gravadas com URL que resolve.

## Fora de escopo

- matching e validação humana (já existem);
- coleta em segundo plano ou agendada;
- abrir a página de cada reclamação para enriquecer o texto;
- qualquer tentativa de contornar proteção do site além de navegar em ritmo humano.

## Aceite

`POST /searches` com `{"terms":["glicosimetro"],"pages":1}` grava reclamações reais, com
URL que resolve, empresa, data e texto preservados; as contagens batem com o que foi
coletado; os testes não dependem de rede.

Ao concluir, `/revisar` e `/documentar` — a mudança de estratégia de extração e a regra
de composição da URL merecem entrada em `docs/DECISIONS.md`.
