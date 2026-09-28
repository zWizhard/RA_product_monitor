"""Pesquisa livre sobre as reclamações já coletadas, com etiqueta de relevância.

A coleta continua em `POST /searches`; esta rota só lê o banco. A etiqueta vem de
`app.relevance`, calculada na hora e nunca gravada.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.collect_repository import ComplaintRepository
from app.db import Database
from app.models import ResearchArea, ResearchItem, ResearchResult
from app.relevance import (
    AREAS,
    AREAS_BY_KEY,
    CATEGORY_LABELS,
    LABEL_ORDER,
    RULES_VERSION,
    classify,
)
from app.taxonomy import Category

router = APIRouter(prefix="/research", tags=["pesquisa"])

# Teto de reclamações etiquetadas por consulta; acima disso a resposta vem `truncated`.
_MAX_SCAN = 10_000


def get_db(request: Request) -> Database:
    return request.app.state.db


Db = Annotated[Database, Depends(get_db)]


@router.get("/areas", response_model=list[ResearchArea])
def areas() -> list[ResearchArea]:
    return [
        ResearchArea(
            key=a.key,
            label=a.label,
            category=a.category,
            category_label=CATEGORY_LABELS[a.category],
        )
        for a in AREAS
    ]


@router.get("", response_model=ResearchResult)
def research(
    db: Db,
    term: Annotated[
        str | None,
        Query(min_length=2, max_length=200, description="Termo pesquisado; vazio = todas"),
    ] = None,
    category: Annotated[
        Category | None, Query(description="Etiqueta só contra as áreas desta categoria")
    ] = None,
    area: Annotated[
        str | None, Query(description="Área para a etiqueta; vazio = a mais próxima")
    ] = None,
    date_from: Annotated[date | None, Query(description="Publicação a partir de")] = None,
    date_to: Annotated[date | None, Query(description="Publicação até, inclusive")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> ResearchResult:
    """Reclamações encontradas por busca com o termo ou que o citam, no período.

    Sem termo, todas as coletadas no período. Ordem: relevantes, duvidosas, fora do
    assunto; dentro de cada grupo, com sinal de evento adverso primeiro e depois da
    publicação mais recente para a mais antiga.
    """
    area = area or None
    if area is not None and area not in AREAS_BY_KEY:
        raise HTTPException(422, f"área '{area}' desconhecida; aceitas: {', '.join(AREAS_BY_KEY)}")
    if area is not None and category is not None and AREAS_BY_KEY[area].category != category:
        raise HTTPException(422, f"área '{area}' não pertence à categoria '{category}'")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, f"date_from ({date_from}) é posterior a date_to ({date_to})")

    repo = ComplaintRepository(db)
    # A etiqueta vem antes do corte: cortar primeiro (pela data de coleta) poderia
    # deixar relevantes de fora e manter as fora do assunto.
    complaints = repo.list(
        term=term, date_from=date_from, date_to=date_to, limit=_MAX_SCAN + 1
    )
    scan_truncated = len(complaints) > _MAX_SCAN
    items = []
    for complaint in complaints[:_MAX_SCAN]:
        result = classify(complaint.title, complaint.description, area, category)
        items.append(
            ResearchItem(
                complaint=complaint,
                relevance=result.label,
                area=result.area,
                context_terms=list(result.context_terms),
                off_topic_terms=list(result.off_topic_terms),
                adverse_signals=list(result.adverse_signals),
            )
        )
    # Duas ordenações estáveis: a data desempata dentro do grupo; sem data vai ao fim.
    items.sort(key=lambda item: item.complaint.published_at or datetime.min, reverse=True)
    items.sort(key=lambda item: (LABEL_ORDER.index(item.relevance), not item.adverse_signals))
    dated_scope = date_from is not None or date_to is not None
    return ResearchResult(
        term=term,
        category=category,
        area=area,
        date_from=date_from,
        date_to=date_to,
        rules_version=RULES_VERSION,
        total=len(items),
        counts={label: sum(item.relevance == label for item in items) for label in LABEL_ORDER},
        adverse=sum(bool(item.adverse_signals) for item in items),
        undated_excluded=repo.count_undated(term=term) if dated_scope else 0,
        truncated=scan_truncated or len(items) > limit,
        items=items[:limit],
    )
