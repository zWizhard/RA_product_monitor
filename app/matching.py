"""
Motor determinístico de matching produto ↔ reclamação.

As camadas são avaliadas na ordem obrigatória e a primeira que produz evidência
decide o método registrado:

1. normalização (`app.normalization` + `tokenize`, que preserva a posição de cada
   token no texto original para que a evidência citada seja o trecho real da fonte,
   e a forma compactada, que iguala "One Touch" a "OneTouch");
2. nome/alias exato;
3. marca/fabricante no título ou no corpo + contexto (modelo, subcategoria, termo do
   cadastro ou vocabulário da categoria);
4. marca atribuída pela empresa da página + contexto de categoria;
5. termos/sinônimos declarados;
6. fuzzy matching sobre nome e aliases.

A camada de IA não foi necessária: nenhuma decisão aqui depende de interpretação de
linguagem natural. Evidência fraca não vira `confirmed` — fica em `possible`, para
revisão humana.

Comparação por sequência de tokens, nunca por substring: "AH" não casa dentro de
"palha" e "ácido hialurônico" só casa com as duas palavras adjacentes. A comparação
compactada segue a mesma regra — junta tokens inteiros e só para comparar com um
termo do catálogo, então "gtechfree" casa com "G-Tech Free" e "gtechfreedom" não.

O que o texto espontâneo do consumidor obrigou a distinguir (ver `docs/DECISIONS.md`):
- a empresa da página do Reclame Aqui é atribuição de marca, não texto qualquer:
  sozinha nunca confirma um modelo, mas recupera o candidato para revisão;
- citar um aparelho no corpo não é reclamar dele: menção comparativa ("era usuário
  do X", "outro modelo (X)") ou marca incompatível com a empresa da página impedem
  confirmação automática — sem penalizar o modelo que só aparece no corpo.

A arbitragem (`arbitrate`) é a única etapa que compara candidatos de produtos
diferentes; por isso roda depois, sobre a reclamação inteira: primeiro a precedência do
nome mais específico, depois a supressão dos irmãos da marca já identificada. As regras
do `det-3` são chaves em `MatchingConfig`, e desligá-las reproduz o `det-2` (`DET2`).

Nada aqui lê banco ou rede: a entrada são objetos já carregados e a saída é um
candidato explicável. Os limiares ficam em `MatchingConfig`, não espalhados no código.

Custo: o pior caso é o par sem match, que percorre todas as camadas até o fuzzy —
~11 ms por par no corpus real de glicosímetros (descrições longas), dos quais ~9 ms
são do fuzzy. Nome, alias, marca e contexto são resolvidos por consulta ao índice da
reclamação, montado uma vez por reclamação e reaproveitado por todos os produtos. Um
reprocessamento grande deve ser fatiado por `limit`/`offset`; `MatchRunResult.truncated`
avisa quando o escopo bateu no teto.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING

from app.normalization import normalize_text
from app.taxonomy import category_context_terms

if TYPE_CHECKING:  # evita ciclo: `app.models` importa os enums definidos aqui
    from app.models import Complaint, Product


class MatchMethod(StrEnum):
    """Como a correspondência foi obtida. É o 'por quê' registrado no match."""

    EXACT_NAME = "exact_name"
    ALIAS = "alias"
    BRAND_CONTEXT = "brand_context"
    # Marca atribuída pela empresa da página da fonte, qualificada por contexto de
    # categoria. Identifica a marca, nunca o modelo: não confirma sozinha.
    COMPANY_BRAND = "company_brand"
    SEARCH_TERM = "search_term"
    FUZZY = "fuzzy"


class MatchStatus(StrEnum):
    """Estados de `docs/DOMAIN.md`. `discarded` só vem de decisão humana."""

    CONFIRMED = "confirmed"
    POSSIBLE = "possible"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class MatchingConfig:
    """Limiares e pesos do matching, em um único lugar.

    `version` entra em cada match gravado: mudar peso ou limiar muda o resultado, e
    saber com que configuração um match foi decidido faz parte da auditoria.
    """

    version: str = "det-3"
    # Regras do `det-3`, desligáveis uma a uma. Desligar as quatro reproduz o `det-2`
    # (ver `DET2`): é assim que uma versão do motor é comparada com a anterior sobre o
    # mesmo conjunto de rótulos, sem depender de código antigo guardado em paralelo.
    #
    # 1. o nome mais específico prevalece, lendo título e corpo juntos e aceitando a
    #    ordem dos tokens trocada ("libre plus 2" para "Libre 2 Plus");
    model_precedence: bool = True
    # 2. candidato vencido por especificidade deixa de ser candidato;
    suppress_shadowed: bool = True
    # 3. identificado um modelo pelo nome, irmãos da mesma marca sem nome próprio saem;
    brand_sibling: bool = True
    # 4. lista ampliada de sinais de citação por comparação ou aferição.
    extended_comparative: bool = True
    # Acima de `confirm_threshold` o match é automático; entre os dois limiares exige
    # revisão humana; abaixo de `review_threshold` não é candidato.
    confirm_threshold: float = 0.85
    review_threshold: float = 0.55
    score_exact_name: float = 0.95
    score_alias: float = 0.90
    score_brand_context: float = 0.70
    # Marca vinda da empresa da página: identifica o fabricante, não o modelo.
    score_company_brand: float = 0.65
    # Termo genérico sozinho fica abaixo do limiar de confirmação, por regra de domínio.
    score_search_term: float = 0.60
    title_bonus: float = 0.03
    fuzzy_min_ratio: float = 0.88
    # Fuzzy nunca confirma sozinho: o teto fica abaixo de `confirm_threshold`.
    fuzzy_max_score: float = 0.84
    # Termo normalizado mais curto que isto é ambíguo demais para sustentar um match.
    min_term_chars: int = 4
    # Contexto qualifica uma marca já encontrada, não sustenta match sozinho: códigos
    # curtos de modelo ("MPT", "III") valem como contexto.
    min_context_chars: int = 2
    evidence_window: int = 120
    # Quanto texto antes da evidência é lido à procura de menção comparativa. Os casos
    # reais ("Era usuário do Libre 2 Plus", "outro modelo (Libre 2 Plus)") trazem o sinal
    # colado ao nome; a janela cobre a oração, não o parágrafo.
    comparative_window: int = 100


MATCHING = MatchingConfig()
# Versão anterior, preservada executável: é o termo de comparação de qualquer mudança
# de motor sobre os rótulos humanos já existentes.
DET2 = MatchingConfig(
    version="det-2",
    model_precedence=False,
    suppress_shadowed=False,
    brand_sibling=False,
    extended_comparative=False,
)

_FIELD_LABEL = {
    "title": "no título",
    "description": "na descrição",
    "company": "no nome da empresa",
}

# Campos onde o consumidor descreve o problema. `company` é atribuição da fonte, não
# texto escrito por ele, e por isso é tratado à parte.
_WRITTEN_FIELDS = ("title", "description")

# Sinais de que o nome citado é outro aparelho — o anterior, o oferecido, o comparado —
# e não o objeto da reclamação. Só valem junto de evidência exclusiva na descrição, e
# só impedem confirmação automática: o par continua indo para revisão humana.
COMPARATIVE_CUES: tuple[str, ...] = (
    "era usuario",
    "era usuaria",
    "fui usuario",
    "fui usuaria",
    "antes usava",
    "antes eu usava",
    "usava antes",
    "ja usei",
    "outro modelo",
    "outra marca",
    "modelo anterior",
    "marca anterior",
    "aparelho anterior",
    "sensor anterior",
    "em vez de",
    "ao inves de",
    "no lugar do",
    "no lugar da",
    "troquei do",
    "troquei para",
    "migrei do",
    "migrei para",
    "mudei do",
    "mudei para",
    "comparado com",
    "comparando com",
    "diferente do",
)

# Acrescentados no `det-3` (`extended_comparative`): o aparelho citado como régua de
# aferição, ou como o segundo aparelho que a pessoa teve de arranjar para provar a falha
# do primeiro. Mesma natureza dos sinais acima — continuam só impedindo confirmação.
COMPARATIVE_CUES_EXTENDED: tuple[str, ...] = (
    "da mesma marca",
    "de outra marca",
    "outro aparelho",
    "segundo aparelho",
    "obrigado a comprar",
    "obrigada a comprar",
    "tive que comprar",
    "precisei comprar",
    "para comparar",
    "para conferir",
    "para confirmar",
    "em paralelo",
    "simultaneo com",
    "simultanea com",
    "confrontado com",
    "aferido com",
    "aferi com",
    "medi tambem",
    "testei tambem",
)


def comparative_cues(config: MatchingConfig) -> tuple[str, ...]:
    """Sinais de citação por comparação em vigor nesta configuração."""
    if config.extended_comparative:
        return COMPARATIVE_CUES + COMPARATIVE_CUES_EXTENDED
    return COMPARATIVE_CUES


# --------------------------------------------------------------- normalização


@dataclass(frozen=True)
class Token:
    """Token normalizado e sua posição no texto original, usada para citar evidência."""

    value: str
    start: int
    end: int


# Tamanho das janelas indexadas por campo. Cobre nome de produto e termo de contexto
# reais ("monitoramento continuo da glicose"); termo maior cai na varredura direta. O
# teto vale também para a forma compactada: um termo só é reencontrado junto se couber
# em até `_MAX_INDEX_TOKENS` tokens do texto ("one touch" para "OneTouch").
_MAX_INDEX_TOKENS = 6
_MAX_INDEX_CHARS = 60

# Janela indexada -> posição do primeiro e do último token dela.
_Spans = dict[str, tuple[int, int]]


@dataclass(frozen=True)
class TextSection:
    """Um campo da reclamação, com o texto original, seus tokens e o índice de janelas.

    O índice é montado uma vez por reclamação e respondido em tempo constante por
    termo: sem ele, cada produto revarreria o texto inteiro para cada nome, alias,
    marca e termo de contexto. `plain` guarda a janela como sequência de tokens e
    `compact` a mesma janela sem separadores — as duas apontam para a primeira
    ocorrência, que é a que vira evidência.
    """

    name: str
    text: str
    tokens: tuple[Token, ...]
    plain: _Spans = field(default_factory=dict, repr=False)
    compact: _Spans = field(default_factory=dict, repr=False)
    # Mesma janela com os tokens ordenados: iguala "libre plus 2" a "Libre 2 Plus". Só
    # janelas de dois tokens ou mais entram, para não transformar token solto em coringa.
    unordered: _Spans = field(default_factory=dict, repr=False)


@lru_cache(maxsize=2048)
def _fold(char: str) -> str:
    """Mesma regra de `normalize_text`, caractere a caractere: sem acento, minúsculo.

    Chamada uma vez por caractere de cada reclamação; o domínio é pequeno (as letras
    que aparecem em texto português), então o cache elimina a decomposição repetida.
    """
    if char.isascii():
        return char.lower() if char.isalnum() else ""
    decomposed = unicodedata.normalize("NFKD", char)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped.casefold() if c.isascii() and c.isalnum())


def tokenize(text: str | None) -> list[Token]:
    """Tokens normalizados, preservando o intervalo de cada um no texto de entrada."""
    tokens: list[Token] = []
    buffer: list[str] = []
    start = end = 0
    for index, char in enumerate(text or ""):
        kept = _fold(char)
        if kept:
            if not buffer:
                start = index
            buffer.append(kept)
            end = index + 1
        elif buffer:
            tokens.append(Token("".join(buffer), start, end))
            buffer = []
    if buffer:
        tokens.append(Token("".join(buffer), start, end))
    return tokens


def _index(tokens: tuple[Token, ...]) -> tuple[_Spans, _Spans, _Spans]:
    """Janelas de até `_MAX_INDEX_TOKENS` tokens: na ordem, compactada e reordenada."""
    plain: _Spans = {}
    compact: _Spans = {}
    unordered: _Spans = {}
    for start in range(len(tokens)):
        joined = ""
        squeezed = ""
        window: list[str] = []
        for end in range(start, min(start + _MAX_INDEX_TOKENS, len(tokens))):
            value = tokens[end].value
            joined = value if end == start else f"{joined} {value}"
            squeezed += value
            window.append(value)
            if len(squeezed) > _MAX_INDEX_CHARS:
                break
            span = (start, end)
            # `setdefault`: a primeira ocorrência é a que vale como evidência.
            plain.setdefault(joined, span)
            compact.setdefault(squeezed, span)
            if end > start:
                unordered.setdefault(" ".join(sorted(window)), span)
    return plain, compact, unordered


def _section(name: str, text: str) -> TextSection:
    tokens = tuple(tokenize(text))
    return TextSection(name, text, tokens, *_index(tokens))


def sections(complaint: Complaint) -> tuple[TextSection, ...]:
    """Campos analisáveis, em ordem de peso: título primeiro, onde vale o bônus."""
    fields = (
        ("title", complaint.title),
        ("description", complaint.description),
        ("company", complaint.company),
    )
    return tuple(_section(name, value) for name, value in fields if value)


def _compact(normalized: str) -> str:
    """Forma sem separadores: iguala "one touch" a "onetouch"."""
    return normalized.replace(" ", "")


@dataclass(frozen=True)
class Catalog:
    """Vocabulário de marcas do escopo monitorado, usado para ler o campo `company`.

    A empresa da página do Reclame Aqui só vira atribuição de marca quando bate com
    uma marca ou fabricante já cadastrado. Farmácia e marketplace não constam do
    catálogo e, por isso, não atribuem marca nenhuma.
    """

    # forma compactada da marca -> grafia como está no cadastro
    brands: dict[str, str] = field(default_factory=dict)


EMPTY_CATALOG = Catalog()


def brand_keys(product: Product) -> frozenset[str]:
    """Chaves de marca do produto: marca e fabricante, na forma compactada."""
    keys = (_compact(normalize_text(value)) for value in (product.brand, product.manufacturer))
    return frozenset(key for key in keys if key)


def build_catalog(products: Iterable[Product]) -> Catalog:
    """Catálogo de marcas do escopo avaliado."""
    brands: dict[str, str] = {}
    for product in products:
        for value in (product.brand, product.manufacturer):
            key = _compact(normalize_text(value))
            if key and key not in brands:
                brands[key] = value  # type: ignore[assignment]
    return Catalog(brands)


@dataclass
class ComplaintText:
    """Reclamação tokenizada uma vez para ser confrontada com vários produtos.

    Tokenizar e recortar janelas depende só da reclamação, não do produto. Preparar
    por reclamação — e não por par — é o que mantém um reprocessamento viável.
    """

    sections: tuple[TextSection, ...]
    # Marcas do catálogo reconhecidas na empresa da página da fonte.
    company_brands: frozenset[str] = frozenset()
    _windows: dict[tuple[str, int], list[str]] = field(default_factory=dict, repr=False)

    def windows(self, section: TextSection, size: int) -> list[str]:
        """Janelas de `size` tokens do campo, como texto normalizado."""
        key = (section.name, size)
        cached = self._windows.get(key)
        if cached is None:
            tokens = section.tokens
            cached = [
                " ".join(token.value for token in tokens[index : index + size])
                for index in range(len(tokens) - size + 1)
            ]
            self._windows[key] = cached
        return cached


def _written(parts: tuple[TextSection, ...]) -> tuple[TextSection, ...]:
    """Campos escritos pelo consumidor. `company` é atribuição da fonte, não relato."""
    return tuple(part for part in parts if part.name in _WRITTEN_FIELDS)


def _attribute_company(
    parts: tuple[TextSection, ...], catalog: Catalog, config: MatchingConfig
) -> frozenset[str]:
    """Marcas do catálogo reconhecidas no nome da empresa da página.

    Nome de empresa é curto e vem da fonte, não do texto livre: o que não bate com o
    catálogo simplesmente não atribui marca — é o caso de farmácia e marketplace.
    """
    company = next((part for part in parts if part.name == "company"), None)
    if company is None or not catalog.brands:
        return frozenset()
    return frozenset(
        key
        for key, spelling in catalog.brands.items()
        if _find_term(spelling, (company,), config) is not None
    )


def prepare(
    complaint: Complaint,
    catalog: Catalog = EMPTY_CATALOG,
    config: MatchingConfig = MATCHING,
) -> ComplaintText:
    """Prepara o texto da reclamação para o confronto com o cadastro.

    `catalog` é o vocabulário de marcas do escopo em avaliação: sem ele, o nome da
    empresa continua sendo só texto. `config` é o mesmo do confronto — ler a empresa com
    limiar diferente do resto do motor seria divergência invisível.
    """
    parts = sections(complaint)
    return ComplaintText(parts, _attribute_company(parts, catalog, config))


# ------------------------------------------------------------------ candidato


@dataclass(frozen=True)
class MatchCandidate:
    """Correspondência encontrada, com tudo que é preciso para explicá-la."""

    method: MatchMethod
    score: float
    matched_term: str
    evidence_field: str
    evidence: str
    explanation: str
    # Onde o termo foi encontrado no campo, em caracteres do texto original. É o que
    # permite saber se dois produtos casaram sobre o mesmo trecho.
    span: tuple[int, int] = (0, 0)
    # Tokens normalizados do nome/alias que casou; vazio quando o método não identifica
    # o produto por nome (marca, termo genérico). Só estes entram na arbitragem.
    term_tokens: tuple[str, ...] = ()
    # Evidência suficiente para a decisão automática confirmar sozinha.
    confirmable: bool = True
    # Produto mais específico que cobre o mesmo trecho, quando houver.
    shadowed_by_product_id: int | None = None
    # Regra do `det-3` que tirou este candidato de campo, quando alguma tirou.
    suppressed_by: str | None = None

    @property
    def identifies_model(self) -> bool:
        """O nome ou alias do próprio produto foi encontrado inteiro no texto.

        É o que separa "a reclamação diz qual é o aparelho" de "a reclamação diz a
        marca": só o primeiro sustenta a precedência sobre os irmãos da marca.
        """
        return self.method in (MatchMethod.EXACT_NAME, MatchMethod.ALIAS)


@dataclass(frozen=True)
class _Hit:
    section: TextSection
    first: Token
    last: Token
    # A ocorrência veio da forma compactada ("One Touch" para "OneTouch").
    compact: bool = False
    # A ocorrência tem os mesmos tokens em outra ordem ("libre plus 2").
    reordered: bool = False

    @property
    def span(self) -> tuple[int, int]:
        return self.first.start, self.last.end


def status_for(score: float, config: MatchingConfig = MATCHING) -> MatchStatus:
    """Decisão automática derivada do score. Aqui `discarded` significa 'não é candidato'."""
    if score >= config.confirm_threshold:
        return MatchStatus.CONFIRMED
    if score >= config.review_threshold:
        return MatchStatus.POSSIBLE
    return MatchStatus.DISCARDED


def decide(candidate: MatchCandidate, config: MatchingConfig = MATCHING) -> MatchStatus:
    """Decisão automática do candidato: o score, limitado pelo que a evidência sustenta."""
    status = status_for(candidate.score, config)
    if status is MatchStatus.CONFIRMED and not candidate.confirmable:
        return MatchStatus.POSSIBLE
    return status


def _normalized_term(term: str | None, config: MatchingConfig, min_chars: int | None) -> str:
    normalized = normalize_text(term)
    floor = config.min_term_chars if min_chars is None else min_chars
    return normalized if len(normalized.replace(" ", "")) >= floor else ""


def _find_sequence(needle: tuple[str, ...], tokens: tuple[Token, ...]) -> tuple[int, int] | None:
    size = len(needle)
    if not size or size > len(tokens):
        return None
    for index in range(len(tokens) - size + 1):
        if tuple(token.value for token in tokens[index : index + size]) == needle:
            return index, index + size - 1
    return None


def _find_compact(compact: str, tokens: tuple[Token, ...]) -> tuple[int, int] | None:
    """Janela de tokens inteiros cuja forma compactada é exatamente `compact`.

    Cresce a janela enquanto couber no tamanho do alvo, então compara. Comparar só
    com um termo conhecido do catálogo — e nunca com pedaço de token — é o que impede
    que a compactação vire concatenação arbitrária de palavras do texto.
    """
    size = len(compact)
    for index in range(len(tokens)):
        joined = ""
        for end in range(index, len(tokens)):
            joined += tokens[end].value
            if len(joined) > size:
                break
            if joined == compact:
                return index, end
    return None


def _find_term(
    term: str | None,
    parts: tuple[TextSection, ...],
    config: MatchingConfig,
    min_chars: int | None = None,
) -> _Hit | None:
    """Primeira ocorrência do termo, como sequência de tokens.

    Campo a campo, três formas em ordem decrescente de fidelidade: exata, compactada
    ("Accuchek Guide" para "Accu-Chek Guide") e — só com `model_precedence` — os mesmos
    tokens reordenados ("libre plus 2" para "Libre 2 Plus"). A reordenada exige dois
    tokens ou mais e a mesma quantidade deles, adjacentes: é variação de escrita do
    mesmo nome, não casamento de palavras soltas espalhadas pelo texto.
    """
    normalized = _normalized_term(term, config, min_chars)
    if not normalized:
        return None
    needle = tuple(normalized.split())
    compact = _compact(normalized)
    # Termo dentro do tamanho indexado é resolvido por consulta; acima dele, varredura.
    indexed = len(needle) <= _MAX_INDEX_TOKENS and len(compact) <= _MAX_INDEX_CHARS
    for section in parts:
        span = (
            section.plain.get(normalized)
            if indexed
            else _find_sequence(needle, section.tokens)
        )
        if span is not None:
            return _Hit(section, section.tokens[span[0]], section.tokens[span[1]])
        span = (
            section.compact.get(compact)
            if indexed
            else _find_compact(compact, section.tokens)
        )
        if span is not None:
            return _Hit(
                section, section.tokens[span[0]], section.tokens[span[1]], compact=True
            )
        if config.model_precedence and indexed and len(needle) > 1:
            span = section.unordered.get(" ".join(sorted(needle)))
            if span is not None:
                return _Hit(
                    section,
                    section.tokens[span[0]],
                    section.tokens[span[1]],
                    reordered=True,
                )
    return None


def _evidence(hit: _Hit, config: MatchingConfig) -> str:
    """Trecho original ao redor da ocorrência: evidência textual, não reconstruída."""
    text = hit.section.text
    start = max(0, hit.first.start - config.evidence_window)
    end = min(len(text), hit.last.end + config.evidence_window)
    snippet = text[start:end].strip()
    return f"{'…' if start > 0 else ''}{snippet}{'…' if end < len(text) else ''}"


def _score(base: float, hit: _Hit, config: MatchingConfig) -> float:
    bonus = config.title_bonus if hit.section.name == "title" else 0.0
    return round(min(base + bonus, 1.0), 4)


def _comparative_cue(hit: _Hit, config: MatchingConfig) -> str | None:
    """Sinal de menção comparativa imediatamente antes da evidência, se houver."""
    text = hit.section.text
    start = max(0, hit.first.start - config.comparative_window)
    lead = tuple(token.value for token in tokenize(text[start : hit.first.start]))
    for cue in comparative_cues(config):
        needle = tuple(cue.split())
        size = len(needle)
        if any(lead[index : index + size] == needle for index in range(len(lead) - size + 1)):
            return cue
    return None


def _confirmability(
    product: Product, hit: _Hit, text: ComplaintText, config: MatchingConfig
) -> tuple[bool, str]:
    """Se a evidência sustenta confirmação automática, e por que não quando não sustenta.

    Ocorrência exata no corpo continua valendo como evidência. O que ela não sustenta
    sozinha é a confirmação automática quando o texto indica outro aparelho — citado
    por comparação ou de marca diferente da que a fonte atribui à reclamação.
    """
    if hit.section.name == "company":
        return False, "evidência apenas no nome da empresa, que identifica marca e não modelo"
    if hit.section.name != "description":
        return True, ""
    if text.company_brands and not (text.company_brands & brand_keys(product)):
        return False, (
            "a empresa da página atribui outra marca e a evidência está apenas na descrição"
        )
    cue = _comparative_cue(hit, config)
    if cue is not None:
        return False, f"menção comparativa ('{cue}') antes da evidência, apenas na descrição"
    return True, ""


def _candidate(
    method: MatchMethod,
    base: float,
    term: str,
    hit: _Hit,
    config: MatchingConfig,
    explanation: str,
    *,
    product: Product,
    text: ComplaintText,
    term_tokens: tuple[str, ...] = (),
    evidence: str | None = None,
) -> MatchCandidate:
    score = _score(base, hit, config)
    confirmable, reason = True, ""
    # Só o que chegaria a `confirmed` precisa ser qualificado; o resto já vai para a fila.
    if hit.section.name == "company" or score >= config.confirm_threshold:
        confirmable, reason = _confirmability(product, hit, text, config)
    reasons = [explanation]
    if hit.compact:
        reasons.append(f"grafia composta equivalente a '{term}'")
    if reason:
        reasons.append(reason)
    return MatchCandidate(
        method=method,
        score=score,
        matched_term=term,
        evidence_field=hit.section.name,
        evidence=evidence or _evidence(hit, config),
        explanation="; ".join(reasons),
        span=hit.span,
        term_tokens=term_tokens,
        confirmable=confirmable,
    )


def _term_tokens(term: str) -> tuple[str, ...]:
    return tuple(normalize_text(term).split())


def _model_tokens(product: Product, context: str) -> tuple[str, ...]:
    """Tokens que identificam o produto quando o contexto que casou é o modelo dele.

    Vocabulário de categoria ("glicosímetro", "sensor") descreve o tipo e não identifica
    nada, então não entra na arbitragem. O modelo, sim: é por isso que o "Guide" de um
    produto perde para o "Smart Guide" de outro na mesma reclamação.
    """
    if product.model and normalize_text(context) == normalize_text(product.model):
        return _term_tokens(context)
    return ()


def context_terms(product: Product) -> list[str]:
    """Sinais que qualificam uma marca já encontrada.

    Vêm do cadastro do produto (modelo, subcategoria, termos declarados) e do
    vocabulário da categoria (`app.taxonomy`) — este último descreve o tipo de produto
    e nunca identifica um modelo: por isso qualifica marca, mas não vira alias.
    """
    terms: list[str] = []
    if product.model:
        terms.append(product.model)
    if product.subcategory:
        terms.append(product.subcategory)
    terms.extend(product.search_terms)
    terms.extend(category_context_terms(product.category, product.subcategory))
    return terms


def _fuzzy(
    product: Product, text: ComplaintText, config: MatchingConfig
) -> MatchCandidate | None:
    """Melhor aproximação de nome/alias sobre janelas do mesmo tamanho, em tokens.

    Esta é a camada mais cara e a que mais roda: quando nada casou antes, ela varre
    todas as janelas de todos os campos. Por isso o termo fica fixo em `seq2` (o
    difflib só reindexa `b` quando essa sequência muda) e cada janela passa antes
    pelos limites superiores baratos `real_quick_ratio`/`quick_ratio` — como são
    limites superiores de `ratio`, descartar por eles não perde nenhum candidato.
    """
    best: tuple[float, str, _Hit] | None = None
    matcher = SequenceMatcher(None)
    for term in (product.name, *product.aliases):
        normalized = _normalized_term(term, config, None)
        if not normalized:
            continue
        size = len(normalized.split())
        matcher.set_seq2(normalized)
        for section in text.sections:
            for index, candidate_text in enumerate(text.windows(section, size)):
                matcher.set_seq1(candidate_text)
                if (
                    matcher.real_quick_ratio() < config.fuzzy_min_ratio
                    or matcher.quick_ratio() < config.fuzzy_min_ratio
                ):
                    continue
                ratio = matcher.ratio()
                if ratio >= config.fuzzy_min_ratio and (best is None or ratio > best[0]):
                    window = section.tokens[index : index + size]
                    best = (ratio, term, _Hit(section, window[0], window[-1]))
    if best is None:
        return None
    ratio, term, hit = best
    score = min(_score(ratio * config.score_exact_name, hit, config), config.fuzzy_max_score)
    return MatchCandidate(
        method=MatchMethod.FUZZY,
        score=round(score, 4),
        matched_term=term,
        evidence_field=hit.section.name,
        evidence=_evidence(hit, config),
        explanation=(
            f"variação aproximada de '{term}' (similaridade {ratio:.2f}) "
            f"{_FIELD_LABEL[hit.section.name]}"
        ),
        span=hit.span,
        # Fuzzy não entra na arbitragem por especificidade: o que casou é uma janela
        # aproximada, não o termo do cadastro.
        confirmable=False,
    )


def _company_brand(
    product: Product, text: ComplaintText, config: MatchingConfig
) -> MatchCandidate | None:
    """Marca atribuída pela empresa da página, qualificada por contexto.

    A página da empresa no Reclame Aqui é atribuição da fonte, não texto do
    consumidor: identifica o fabricante com segurança, mas não diz qual modelo. Por
    isso recupera o candidato para revisão e nunca confirma sozinha.
    """
    company = next((part for part in text.sections if part.name == "company"), None)
    if company is None:
        return None
    for brand in (product.brand, product.manufacturer):
        brand_hit = _find_term(brand, (company,), config)
        if brand_hit is None:
            continue
        for context in context_terms(product):
            context_hit = _find_term(context, text.sections, config, config.min_context_chars)
            if context_hit is None:
                continue
            brand_evidence = _evidence(brand_hit, config)
            context_evidence = _evidence(context_hit, config)
            evidence = brand_evidence
            if context_evidence != brand_evidence:
                evidence = f"{brand_evidence} | {context_evidence}"
            return _candidate(
                MatchMethod.COMPANY_BRAND,
                config.score_company_brand,
                f"{brand} + {context}",
                brand_hit,
                config,
                f"a empresa da reclamação ('{company.text}') é a marca '{brand}' do cadastro, "
                f"com o contexto '{context}' {_FIELD_LABEL[context_hit.section.name]}",
                product=product,
                text=text,
                term_tokens=_model_tokens(product, context),
                evidence=evidence,
            )
    return None


def _more_specific(candidate: MatchCandidate, other: MatchCandidate, config: MatchingConfig) -> bool:
    """`other` identifica o produto por um nome que contém o de `candidate`."""
    if not candidate.term_tokens or not other.term_tokens:
        return False
    if len(other.term_tokens) <= len(candidate.term_tokens):
        return False
    if config.model_precedence:
        # Título e corpo lidos juntos, e a ordem dos tokens não decide: "Libre 2 Plus"
        # contém "Libre" tanto em "libre 2 plus" quanto em "libre plus 2".
        return set(candidate.term_tokens) < set(other.term_tokens)
    return (
        _find_sequence_in(candidate.term_tokens, other.term_tokens)
        and candidate.evidence_field == other.evidence_field
        and _overlaps(candidate.span, other.span)
    )


def arbitrate(
    entries: Sequence[tuple[Product, MatchCandidate]], config: MatchingConfig = MATCHING
) -> list[tuple[Product, MatchCandidate]]:
    """Arbitragem entre os candidatos de uma mesma reclamação, na ordem obrigatória.

    1. **Especificidade.** Quem casou o nome mais curto está sendo arrastado pelo mais
       específico: "FreeStyle Libre" dentro de "FreeStyle Libre 2 Plus". No `det-2` isso
       exigia o mesmo trecho e apenas rebaixava; com `model_precedence` vale para a
       reclamação inteira, porque o consumidor escreve a família no título e o modelo no
       corpo, e com `suppress_shadowed` o perdedor deixa de ser candidato — a validação
       humana dos 10 primeiros casos não sustentou nenhum deles.
    2. **Irmão da mesma marca.** Identificado um modelo pelo nome, outro produto da mesma
       marca que só tenha marca+contexto não é candidato: é a mesma marca aparecendo duas
       vezes, não dois aparelhos. Depende da etapa anterior — "quem foi nomeado" tem de
       ser o vencedor da especificidade, nunca a família derrotada.

    O candidato suprimido sai com `suppressed_by` preenchido; quem persiste decide o que
    fazer com ele. Nada aqui apaga evidência.
    """
    resolved: list[tuple[Product, MatchCandidate]] = []
    for product, candidate in entries:
        winner = next(
            (
                other_product
                for other_product, other in entries
                if other_product.id != product.id and _more_specific(candidate, other, config)
            ),
            None,
        )
        if winner is None:
            resolved.append((product, candidate))
            continue
        shadowed = replace(
            candidate,
            confirmable=False,
            shadowed_by_product_id=winner.id,
            explanation=(
                f"{candidate.explanation}; rebaixado por especificidade: '{winner.name}' "
                "casou um nome mais específico nesta reclamação"
            ),
        )
        if config.suppress_shadowed:
            shadowed = replace(shadowed, suppressed_by="especificidade")
        resolved.append((product, shadowed))

    if not config.brand_sibling:
        return resolved

    # Marcas cujo modelo foi identificado pelo nome — já descontada a especificidade.
    nomeadas = {
        product.brand
        for product, candidate in resolved
        if product.brand and candidate.identifies_model and candidate.suppressed_by is None
    }
    if not nomeadas:
        return resolved
    final: list[tuple[Product, MatchCandidate]] = []
    for product, candidate in resolved:
        if (
            candidate.suppressed_by is None
            and not candidate.identifies_model
            and product.brand in nomeadas
        ):
            candidate = replace(
                candidate,
                confirmable=False,
                suppressed_by="irmao_de_marca",
                explanation=(
                    f"{candidate.explanation}; suprimido: outro modelo '{product.brand}' foi "
                    "identificado pelo nome nesta reclamação"
                ),
            )
        final.append((product, candidate))
    return final


def match_complaint(
    complaint: Complaint,
    products: Sequence[Product],
    catalog: Catalog,
    config: MatchingConfig = MATCHING,
) -> list[tuple[Product, MatchCandidate]]:
    """O motor inteiro aplicado a uma reclamação: prepara, confronta, arbitra.

    Preparar o texto uma vez por reclamação — e não por par — é o que mantém um
    reprocessamento grande viável; arbitrar só depois de confrontar todos os produtos do
    escopo é obrigatório, porque a arbitragem compara candidatos de produtos diferentes.

    O candidato suprimido vem na lista com `suppressed_by` preenchido: quem chama decide
    o que fazer com ele — persistir (`app.match_service`) ou medir (`app.evaluation`).
    Nada aqui lê banco nem rede.
    """
    text = prepare(complaint, catalog, config)
    found = [
        (product, candidate)
        for product in products
        if (candidate := match_prepared(product, text, config)) is not None
    ]
    return arbitrate(found, config)


def _find_sequence_in(needle: tuple[str, ...], haystack: tuple[str, ...]) -> bool:
    size = len(needle)
    limit = len(haystack) - size + 1
    return any(haystack[index : index + size] == needle for index in range(limit))


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def match_product(
    product: Product, complaint: Complaint, config: MatchingConfig = MATCHING
) -> MatchCandidate | None:
    """Candidato entre um produto e uma reclamação, ou `None`.

    Conveniência para uso avulso. Em lote, prepare a reclamação uma vez com
    `prepare` e use `match_prepared`.
    """
    text = prepare(complaint, build_catalog([product]), config)
    return match_prepared(product, text, config)


def match_prepared(
    product: Product, text: ComplaintText, config: MatchingConfig = MATCHING
) -> MatchCandidate | None:
    """Candidato de correspondência sobre uma reclamação já preparada, ou `None`.

    Devolver um candidato não é confirmar o match: isso é decidido por `status_for`.
    """
    parts = text.sections
    if not parts:
        return None
    written = _written(parts)

    hit = _find_term(product.name, parts, config)
    if hit is not None:
        return _candidate(
            MatchMethod.EXACT_NAME,
            config.score_exact_name,
            product.name,
            hit,
            config,
            f"nome do produto '{product.name}' encontrado {_FIELD_LABEL[hit.section.name]}",
            product=product,
            text=text,
            term_tokens=_term_tokens(product.name),
        )

    for alias in product.aliases:
        hit = _find_term(alias, parts, config)
        if hit is not None:
            return _candidate(
                MatchMethod.ALIAS,
                config.score_alias,
                alias,
                hit,
                config,
                f"alias '{alias}' do produto '{product.name}' encontrado "
                f"{_FIELD_LABEL[hit.section.name]}",
                product=product,
                text=text,
                term_tokens=_term_tokens(alias),
            )

    # A marca no texto escrito pelo consumidor. A marca vinda da empresa da página é
    # outra coisa, com outro peso, e vem depois.
    for brand in (product.brand, product.manufacturer):
        brand_hit = _find_term(brand, written, config)
        if brand_hit is None:
            continue
        for context in context_terms(product):
            context_hit = _find_term(context, parts, config, config.min_context_chars)
            if context_hit is None:
                continue
            brand_evidence = _evidence(brand_hit, config)
            context_evidence = _evidence(context_hit, config)
            evidence = brand_evidence
            if context_evidence != brand_evidence:
                evidence = f"{brand_evidence} | {context_evidence}"
            return _candidate(
                MatchMethod.BRAND_CONTEXT,
                config.score_brand_context,
                f"{brand} + {context}",
                brand_hit,
                config,
                f"marca/fabricante '{brand}' {_FIELD_LABEL[brand_hit.section.name]} junto do "
                f"contexto '{context}' {_FIELD_LABEL[context_hit.section.name]}",
                product=product,
                text=text,
                term_tokens=_model_tokens(product, context),
                evidence=evidence,
            )

    company = _company_brand(product, text, config)
    if company is not None:
        return company

    for term in product.search_terms:
        hit = _find_term(term, parts, config)
        if hit is not None:
            return _candidate(
                MatchMethod.SEARCH_TERM,
                config.score_search_term,
                term,
                hit,
                config,
                f"termo '{term}' do cadastro de '{product.name}' encontrado "
                f"{_FIELD_LABEL[hit.section.name]}; termo isolado não confirma",
                product=product,
                text=text,
            )

    return _fuzzy(product, text, config)
