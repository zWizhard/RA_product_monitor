"""Consulta das reclamações coletadas.

Somente leitura: reclamação nasce de uma coleta e preserva a fonte original.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.collect_repository import ComplaintRepository
from app.models import Complaint, ComplaintDetail

router = APIRouter(prefix="/complaints", tags=["reclamações"])


def get_repository(request: Request) -> ComplaintRepository:
    return ComplaintRepository(request.app.state.db)


Repo = Annotated[ComplaintRepository, Depends(get_repository)]


@router.get("", response_model=list[Complaint])
def list_complaints(
    repo: Repo,
    source: str | None = None,
    search_run_id: int | None = None,
    product_id: int | None = None,
    q: Annotated[str | None, Query(description="Busca no título e na descrição")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Complaint]:
    return repo.list(
        source=source,
        search_run_id=search_run_id,
        product_id=product_id,
        query=q,
        limit=limit,
        offset=offset,
    )


@router.get("/{complaint_id}", response_model=ComplaintDetail)
def get_complaint(complaint_id: int, repo: Repo) -> ComplaintDetail:
    """Inclui `found_by`: a busca e o termo que encontraram a reclamação."""
    return repo.detail(complaint_id)
