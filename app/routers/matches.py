"""Matching produto ↔ reclamação: reprocessamento, consulta e validação humana.

Cada match devolvido carrega `explanation`, `matched_term` e `evidence` — a resposta a
"por que este produto foi associado a esta reclamação?".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.db import Database
from app.match_repository import MatchRepository
from app.match_service import run_matching
from app.matching import MatchStatus
from app.models import (
    MatchRequest,
    MatchRevision,
    MatchReview,
    MatchReviewItem,
    MatchRunResult,
    ProductComplaintMatch,
    ReviewQueueScope,
)
from app.taxonomy import Category

router = APIRouter(prefix="/matches", tags=["matching"])


def get_db(request: Request) -> Database:
    return request.app.state.db


Db = Annotated[Database, Depends(get_db)]


@router.post("/run", response_model=MatchRunResult)
def run(data: MatchRequest, db: Db) -> MatchRunResult:
    """Reprocessa o escopo pedido. Idempotente: repetir não duplica nem revisa nada."""
    return run_matching(db, data)


@router.get("", response_model=list[ProductComplaintMatch])
def list_matches(
    db: Db,
    product_id: int | None = None,
    complaint_id: int | None = None,
    status: Annotated[
        MatchStatus | None, Query(description="Decisão que vale: a humana quando existe")
    ] = None,
    method: str | None = None,
    reviewed: Annotated[bool | None, Query(description="Já passou por revisão humana")] = None,
    stale: Annotated[
        bool | None, Query(description="Perdeu a evidência em um reprocessamento")
    ] = None,
    pending: Annotated[
        bool | None, Query(description="Aguarda decisão humana: vigente e ainda `possible`")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductComplaintMatch]:
    return MatchRepository(db).list(
        product_id=product_id,
        complaint_id=complaint_id,
        decision=status,
        method=method,
        reviewed=reviewed,
        stale=stale,
        pending=pending,
        limit=limit,
        offset=offset,
    )


# Precisa vir antes de `/{match_id}`: declarada depois, "queue" cairia no parâmetro
# de caminho e a rota responderia 422.
@router.get("/queue", response_model=list[MatchReviewItem])
def review_queue(
    db: Db,
    product_id: int | None = None,
    category: Category | None = None,
    scope: Annotated[
        ReviewQueueScope, Query(description="Pendentes, já decididos ou ambos")
    ] = ReviewQueueScope.PENDING,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MatchReviewItem]:
    """Fila de revisão humana, do score mais alto para o mais baixo.

    Traz produto candidato e reclamação junto do par, para o analista decidir sem
    precisar abrir outra rota. A reclamação vai como coletada e não é editável.
    """
    return MatchRepository(db).queue(
        product_id=product_id,
        category=category,
        pending=scope.pending,
        limit=limit,
        offset=offset,
    )


@router.get("/{match_id}", response_model=ProductComplaintMatch)
def get_match(match_id: int, db: Db) -> ProductComplaintMatch:
    return MatchRepository(db).get(match_id)


@router.get("/{match_id}/revisions", response_model=list[MatchRevision])
def match_revisions(match_id: int, db: Db) -> list[MatchRevision]:
    """Decisões automáticas anteriores deste par, da mais antiga para a mais recente.

    É por aqui que se recupera o que uma versão anterior do motor havia decidido, com
    a evidência de então, depois de um reprocessamento em nova `matcher_version`.
    """
    repository = MatchRepository(db)
    repository.get(match_id)
    return repository.revisions(match_id)


@router.post("/{match_id}/review", response_model=ProductComplaintMatch)
def review_match(match_id: int, data: MatchReview, db: Db) -> ProductComplaintMatch:
    """Decisão humana. Prevalece sobre a automática e sobrevive ao reprocessamento."""
    return MatchRepository(db).review(
        match_id, status=data.status, reviewed_by=data.reviewed_by, note=data.note
    )
