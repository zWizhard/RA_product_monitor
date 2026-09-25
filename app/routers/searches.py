"""Execução e histórico de buscas no Reclame Aqui.

A coleta é síncrona: a resposta já traz as métricas da execução. Buscas longas
(muitas páginas) devem ser feitas com `pages` baixo por chamada.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.collect_repository import SearchRunRepository
from app.collect_service import run_searches
from app.collector import Collector
from app.db import Database
from app.models import SearchRequest, SearchRun

router = APIRouter(prefix="/searches", tags=["buscas"])


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_collector(request: Request) -> Collector:
    return request.app.state.collector


Db = Annotated[Database, Depends(get_db)]
Coll = Annotated[Collector, Depends(get_collector)]


@router.post("", response_model=list[SearchRun])
async def create_search(data: SearchRequest, db: Db, collector: Coll) -> list[SearchRun]:
    return await run_searches(db, data, collector)


@router.get("", response_model=list[SearchRun])
def list_searches(
    db: Db,
    product_id: int | None = None,
    status: Annotated[str | None, Query(pattern="^(running|completed|failed)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SearchRun]:
    return SearchRunRepository(db).list(
        product_id=product_id, status=status, limit=limit, offset=offset
    )


@router.get("/{run_id}", response_model=SearchRun)
def get_search(run_id: int, db: Db) -> SearchRun:
    return SearchRunRepository(db).get(run_id)
