"""Schemas de produto, busca, reclamação e métricas, usados pela API e pela persistência."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.matching import MatchMethod, MatchStatus
from app.normalization import dedupe_terms
from app.taxonomy import Category, validate_pair

_BASE_CONFIG = ConfigDict(str_strip_whitespace=True, extra="forbid")
_MAX_TERM_LEN = 200


def _blank_to_none(value: str | None) -> str | None:
    """Campo opcional em branco é ausência de informação, não string vazia."""
    return value or None


def _clean_term_list(values: list[str]) -> list[str]:
    cleaned = dedupe_terms(values)
    for term in cleaned:
        if len(term) > _MAX_TERM_LEN:
            raise ValueError(f"termo excede {_MAX_TERM_LEN} caracteres: {term[:50]}…")
    return cleaned


class ProductCreate(BaseModel):
    """Dados aceitos no cadastro. `aliases` e `search_terms` são listas de texto livre."""

    model_config = _BASE_CONFIG

    name: str = Field(min_length=2, max_length=200)
    category: Category
    subcategory: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=150)
    manufacturer: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=150)
    regulatory_id: str | None = Field(default=None, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    active: bool = True

    @field_validator("subcategory", "brand", "manufacturer", "model", "regulatory_id")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @field_validator("aliases", "search_terms")
    @classmethod
    def _clean_terms(cls, values: list[str]) -> list[str]:
        return _clean_term_list(values)

    @model_validator(mode="after")
    def _check_taxonomy(self) -> ProductCreate:
        self.subcategory = validate_pair(self.category, self.subcategory)
        return self


class ProductUpdate(BaseModel):
    """Atualização parcial: apenas os campos enviados são alterados."""

    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=2, max_length=200)
    category: Category | None = None
    subcategory: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=150)
    manufacturer: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=150)
    regulatory_id: str | None = Field(default=None, max_length=100)
    aliases: list[str] | None = None
    search_terms: list[str] | None = None
    active: bool | None = None

    @field_validator("subcategory", "brand", "manufacturer", "model", "regulatory_id")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @field_validator("aliases", "search_terms")
    @classmethod
    def _clean_terms(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _clean_term_list(values)


class Product(BaseModel):
    """Produto persistido, com a chave natural usada no controle de duplicidade."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    category: Category
    subcategory: str | None
    brand: str | None
    manufacturer: str | None
    model: str | None
    regulatory_id: str | None
    aliases: list[str]
    search_terms: list[str]
    active: bool
    natural_key: str
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------- busca e coleta


class SearchRequest(BaseModel):
    """Pedido de coleta: um `search_run` por termo.

    Com `product_id` e sem `terms`, os termos saem do próprio cadastro: os termos de
    busca do produto, ou o nome dele se não houver nenhum.
    """

    model_config = _BASE_CONFIG

    product_id: int | None = None
    terms: list[str] | None = None
    pages: int = Field(default=1, ge=1, le=10)

    @field_validator("terms")
    @classmethod
    def _clean_terms(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _clean_term_list(values)

    @model_validator(mode="after")
    def _require_origin(self) -> SearchRequest:
        if self.product_id is None and not self.terms:
            raise ValueError("informe product_id ou terms")
        return self


class SearchRun(BaseModel):
    """Execução de busca e as métricas do que ela produziu.

    `collected` = `inserted` + `duplicates`; `found` = `collected` + descartes.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    product_id: int | None
    term: str
    term_normalized: str
    source: str
    status: str
    pages_requested: int
    found: int
    collected: int
    inserted: int
    duplicates: int
    failures: int
    notes: list[str]
    started_at: datetime
    finished_at: datetime | None


class ComplaintCreate(BaseModel):
    """Reclamação pronta para persistir, já validada pela camada determinística."""

    model_config = _BASE_CONFIG

    source: str
    external_id: str | None = None
    url: str
    title: str = Field(min_length=3, max_length=300)
    description: str | None = None
    company: str | None = None
    location: str | None = None
    status: str | None = None
    published_at: datetime | None = None
    published_text: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Complaint(BaseModel):
    """Reclamação persistida, com a fonte original preservada."""

    model_config = ConfigDict(extra="forbid")

    id: int
    source: str
    external_id: str | None
    url: str
    dedupe_key: str
    title: str
    description: str | None
    company: str | None
    location: str | None
    status: str | None
    published_at: datetime | None
    published_text: str | None
    collected_at: datetime
    last_seen_at: datetime


class SearchHit(BaseModel):
    """Vínculo entre a execução de busca e a reclamação que ela encontrou."""

    model_config = ConfigDict(extra="forbid")

    search_run_id: int
    term: str
    product_id: int | None
    page: int | None
    position: int | None
    first_seen: bool
    created_at: datetime


class ComplaintDetail(Complaint):
    """Reclamação com a procedência: quais buscas a encontraram."""

    found_by: list[SearchHit]


# ------------------------------------------------------------------- matching


class ProductComplaintMatch(BaseModel):
    """Relação auditável produto ↔ reclamação.

    `status` é a decisão automática do motor; `reviewed_status` é a decisão humana,
    quando existe. `decision` é a que vale — a humana prevalece e nunca é sobrescrita
    por reprocessamento.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    product_id: int
    complaint_id: int
    method: MatchMethod
    score: float
    matched_term: str
    evidence_field: str
    evidence: str
    explanation: str
    status: MatchStatus
    matcher_version: str
    reviewed_status: MatchStatus | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    # Datado quando o reprocessamento deixa de encontrar evidência para o par. A
    # evidência original continua gravada; o match nunca é apagado.
    stale_since: datetime | None
    # Por que ficou obsoleto: perdeu a evidência, ou foi suprimido por uma regra de
    # arbitragem — e qual delas.
    stale_reason: str | None
    # Produto mais específico que cobre o mesmo trecho desta reclamação. Enquanto
    # houver um, a decisão automática não confirma sozinha.
    shadowed_by_product_id: int | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def decision(self) -> MatchStatus:
        return self.reviewed_status or self.status

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pending(self) -> bool:
        """Espera decisão humana: vigente e ainda em `possible`.

        Vale tanto para o que o motor deixou em dúvida quanto para o que um analista
        revisou e manteve em dúvida. Par obsoleto não entra na fila.
        """
        return self.stale_since is None and self.decision is MatchStatus.POSSIBLE


class MatchRevision(BaseModel):
    """Decisão automática anterior de um par, preservada antes de ser reescrita.

    É a proveniência da versão do motor: mostra o que `matcher_version` decidiu, com
    qual evidência, e quando aquela decisão foi substituída. Nunca guarda decisão
    humana — essa não é reescrita por reprocessamento.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    match_id: int
    product_id: int
    complaint_id: int
    method: MatchMethod
    score: float
    matched_term: str
    evidence_field: str
    evidence: str
    explanation: str
    status: MatchStatus
    matcher_version: str
    shadowed_by_product_id: int | None
    stale_since: datetime | None
    stale_reason: str | None
    decided_at: datetime
    superseded_at: datetime


class ReviewProduct(BaseModel):
    """Produto candidato, no que o analista precisa para decidir."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    category: Category
    subcategory: str | None
    brand: str | None
    manufacturer: str | None
    model: str | None
    active: bool


class ReviewComplaint(BaseModel):
    """Reclamação como coletada. Somente leitura: a fonte original não se edita aqui."""

    model_config = ConfigDict(extra="forbid")

    id: int
    source: str
    url: str
    title: str
    description: str | None
    company: str | None
    location: str | None
    published_at: datetime | None
    collected_at: datetime


class ReviewQueueScope(StrEnum):
    """O que a fila de revisão devolve."""

    PENDING = "pending"  # aguarda decisão humana
    DECIDED = "decided"  # já decidido, para reexame
    ALL = "all"

    @property
    def pending(self) -> bool | None:
        """Filtro correspondente em `MatchRepository.queue`; `None` não filtra."""
        return None if self is ReviewQueueScope.ALL else self is ReviewQueueScope.PENDING


class MatchReviewItem(BaseModel):
    """Item da fila de revisão: o par, o produto candidato e a reclamação.

    Reúne em uma resposta o que sustenta a decisão — score, método, termo e o trecho
    de evidência vêm em `match`.
    """

    model_config = ConfigDict(extra="forbid")

    match: ProductComplaintMatch
    product: ReviewProduct
    complaint: ReviewComplaint


class MatchRequest(BaseModel):
    """Escopo de um reprocessamento de matching.

    Sem filtros, avalia os produtos ativos contra as reclamações mais recentes.
    Reprocessar é seguro: o mesmo par produz o mesmo match, sem duplicar linha.
    """

    model_config = _BASE_CONFIG

    product_id: int | None = None
    complaint_id: int | None = None
    search_run_id: int | None = None
    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class MatchRunResult(BaseModel):
    """Métricas de um reprocessamento, com o efeito sobre o que já estava gravado."""

    model_config = ConfigDict(extra="forbid")

    products_evaluated: int
    complaints_evaluated: int
    pairs_evaluated: int
    matched: int
    created: int
    updated: int
    unchanged: int
    # Pares que tinham match e deixaram de ter evidência nesta execução.
    stale: int
    confirmed: int
    possible: int
    matcher_version: str
    # O escopo bateu em um teto (`limit` de reclamações ou o de produtos): há mais a
    # reprocessar, e o chamador precisa fatiar em vez de supor que terminou.
    truncated: bool


class MatchReview(BaseModel):
    """Decisão humana sobre um match."""

    model_config = _BASE_CONFIG

    status: MatchStatus
    reviewed_by: str = Field(min_length=2, max_length=150)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def _normalize_note(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


# ------------------------------------------------------------------ dashboard


class Granularity(StrEnum):
    """Passo do eixo temporal. Fechado: o backend só agrega o que sabe agregar."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class CategoryCount(BaseModel):
    """Produtos monitorados em um par categoria/subcategoria."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    subcategory: str | None
    total: int
    active: int


class StatusCount(BaseModel):
    """Reclamações por situação, como o Reclame Aqui a publica.

    `status` nulo é ausência de informação na fonte, não uma categoria própria.
    """

    model_config = ConfigDict(extra="forbid")

    status: str | None
    complaints: int


class ProductMetrics(BaseModel):
    """Produtos monitorados no escopo. Não depende do recorte temporal."""

    model_config = ConfigDict(extra="forbid")

    total: int
    active: int
    by_category: list[CategoryCount]


class ComplaintMetrics(BaseModel):
    """Reclamações no escopo.

    `undated` é o volume que a fonte publicou sem data legível: ele conta no total
    quando não há recorte temporal, e é justamente o que nenhuma série temporal
    consegue mostrar. Por isso vem sempre, calculado sem o recorte.
    """

    model_config = ConfigDict(extra="forbid")

    total: int
    undated: int
    # Das reclamações no escopo, quantas têm correspondência vigente e não descartada.
    linked: int
    first_published: datetime | None
    last_published: datetime | None
    first_collected: datetime | None
    last_collected: datetime | None
    by_status: list[StatusCount]


class MatchMetrics(BaseModel):
    """Correspondências no escopo, pela decisão que vale.

    `confirmed`, `possible` e `discarded` contam apenas pares vigentes e somam
    `current`. `pending` é o subconjunto de `possible` que espera decisão humana.
    """

    model_config = ConfigDict(extra="forbid")

    total: int
    current: int
    stale: int
    confirmed: int
    possible: int
    discarded: int
    reviewed: int
    pending: int


class SearchMetrics(BaseModel):
    """Execuções de busca no escopo, pela data de execução."""

    model_config = ConfigDict(extra="forbid")

    runs: int
    completed: int
    failed: int
    inserted: int
    last_run_at: datetime | None


class DashboardFiltersEcho(BaseModel):
    """Escopo efetivamente aplicado, como o backend o entendeu."""

    model_config = ConfigDict(extra="forbid")

    product_id: int | None
    category: Category | None
    subcategory: str | None
    date_from: date | None
    date_to: date | None


class DashboardOverview(BaseModel):
    """Contadores do escopo pedido, todos contados pelo banco.

    O recorte temporal se aplica à data de publicação da reclamação — e, em
    `searches`, à data de execução da busca. Produtos não são datados por reclamação
    e por isso ignoram o recorte.
    """

    model_config = ConfigDict(extra="forbid")

    filters: DashboardFiltersEcho
    products: ProductMetrics
    complaints: ComplaintMetrics
    matches: MatchMetrics
    searches: SearchMetrics


class ProductComplaintCount(BaseModel):
    """Produto citado e o volume de reclamações associadas a ele no escopo."""

    model_config = ConfigDict(extra="forbid")

    product_id: int
    name: str
    brand: str | None
    category: Category
    subcategory: str | None
    # Reclamações distintas com correspondência vigente e não descartada.
    complaints: int
    confirmed: int
    possible: int
    pending: int


class TimelineBucket(BaseModel):
    """Um passo da série temporal, pela data de publicação da reclamação."""

    model_config = ConfigDict(extra="forbid")

    period_start: date
    complaints: int
    confirmed: int


class Timeline(BaseModel):
    """Série temporal de reclamações no escopo.

    Os passos vazios entre o primeiro e o último são preenchidos com zero, para que
    a ausência de reclamação não apareça como continuidade. `undated` fica fora da
    série: é o que não tem data para ser posicionado.
    """

    model_config = ConfigDict(extra="forbid")

    granularity: Granularity
    filters: DashboardFiltersEcho
    buckets: list[TimelineBucket]
    undated: int


class TrendWindow(BaseModel):
    """Uma das duas janelas comparadas, com a coleta que a alcançou.

    `searches` é quantas execuções de busca aconteceram dentro da janela. Janela sem
    busca nenhuma só foi vista por uma coleta posterior, e o que se sabe sobre ela
    depende do que aquela coleta trouxe — por isso o número vem junto da contagem.
    """

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    complaints: int
    searches: int


class Trend(BaseModel):
    """Variação entre duas janelas de igual duração, com o teste que a sustenta.

    As janelas terminam na última coleta, não em `date_to`: dia posterior à última
    busca não tem reclamação no banco por construção, e contá-lo simularia queda. A
    janela usada de fato volta em `current`.

    Duas condições independentes precisam valer para haver tendência a afirmar:
    `comparable` (a coleta alcançou as duas janelas) e `significant` (a diferença não
    é compatível com oscilação). `direction` só é preenchida quando as duas valem;
    enquanto isso não acontece, há variação numérica e nenhuma tendência, e `note` diz
    qual delas faltou — as duas, quando as duas falharam. Nada aqui é projetado para o
    futuro.
    """

    model_config = ConfigDict(extra="forbid")

    days: int
    filters: DashboardFiltersEcho
    current: TrendWindow
    previous: TrendWindow
    delta: int
    # Nulo quando a janela anterior não teve reclamação: variação relativa a zero
    # não é número.
    delta_pct: float | None
    p_value: float
    significant: bool
    # As duas janelas tiveram coleta própria. Sem isso, comparar volume mede também
    # quando se coletou, não só o que foi publicado.
    comparable: bool
    direction: str | None
    # O que foi contado e sob qual hipótese o teste vale.
    basis: str
    note: str | None
