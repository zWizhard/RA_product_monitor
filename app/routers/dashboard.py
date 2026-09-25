"""Métricas do dashboard.

Somente leitura e sem cálculo próprio: o endpoint traduz o escopo pedido e devolve o
que `app.analytics` contou no banco. Toda rota aceita o mesmo conjunto de filtros
(produto, categoria/subcategoria e recorte de datas), para que os números exibidos
lado a lado se refiram ao mesmo recorte.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.analytics import AnalyticsRepository, DashboardFilters
from app.models import (
    DashboardOverview,
    Granularity,
    ProductComplaintCount,
    Timeline,
    Trend,
)
from app.taxonomy import Category

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def get_filters(
    product_id: int | None = None,
    category: Category | None = None,
    subcategory: str | None = None,
    date_from: Annotated[
        date | None, Query(description="Início do recorte, inclusive")
    ] = None,
    date_to: Annotated[date | None, Query(description="Fim do recorte, inclusive")] = None,
) -> DashboardFilters:
    """Escopo comum a todas as rotas do dashboard.

    O recorte de datas incide sobre a **publicação** da reclamação; em `searches`, sobre
    a data de execução da busca. O escopo aplicado volta em `filters` na resposta.

    Recorte invertido é erro de quem pergunta, não recorte vazio: sem isto, o dashboard
    responderia zero em tudo como se o banco não tivesse dados.
    """
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, f"date_from ({date_from}) é posterior a date_to ({date_to})")
    return DashboardFilters(
        product_id=product_id,
        category=category,
        subcategory=subcategory,
        date_from=date_from,
        date_to=date_to,
    )


def get_repository(request: Request) -> AnalyticsRepository:
    return AnalyticsRepository(request.app.state.db)


Repo = Annotated[AnalyticsRepository, Depends(get_repository)]
Filters = Annotated[DashboardFilters, Depends(get_filters)]


@router.get("/overview", response_model=DashboardOverview)
def overview(repo: Repo, filters: Filters) -> DashboardOverview:
    """Contadores de produtos, reclamações, correspondências, pendências e buscas."""
    return repo.overview(filters)


@router.get("/top-products", response_model=list[ProductComplaintCount])
def top_products(
    repo: Repo,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> list[ProductComplaintCount]:
    """Produtos mais citados no escopo, por reclamações distintas associadas a eles."""
    return repo.top_products(filters, limit=limit)


@router.get("/timeline", response_model=Timeline)
def timeline(
    repo: Repo,
    filters: Filters,
    granularity: Granularity = Granularity.MONTH,
) -> Timeline:
    """Evolução temporal das reclamações do escopo, pela data de publicação."""
    return repo.timeline(filters, granularity=granularity)


@router.get("/trend", response_model=Trend)
def trend(
    repo: Repo,
    filters: Filters,
    days: Annotated[int, Query(ge=7, le=365, description="Duração de cada janela")] = 90,
) -> Trend:
    """Variação contra o período anterior de igual duração.

    `date_from` não participa: a duração das janelas é `days` e o fim da janela atual é
    `date_to` (ou hoje). A resposta só afirma direção quando a diferença passa o teste;
    quando não passa, `note` diz por quê.
    """
    return repo.trend(filters, days=days)
