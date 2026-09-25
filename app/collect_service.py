"""
Orquestração da coleta: termo -> busca -> reclamações -> métricas.

Uma execução por termo. As contagens de `search_run` têm significado fixo:

- `found`: itens detectados nos resultados, antes de qualquer validação;
- `collected`: itens válidos, com URL real da fonte (`found` menos os descartes);
- `inserted`: reclamações novas gravadas;
- `duplicates`: itens válidos já conhecidos — de execução anterior ou repetidos
  dentro da própria busca;
- `failures`: descartes de item mais falhas de página (timeout, navegação, seletor).

Portanto `collected = inserted + duplicates` e `found = collected + descartes`.
"""

from __future__ import annotations

from app.collect_repository import ComplaintRepository, SearchRunRepository
from app.collector import Collector, parse_items
from app.db import Database
from app.models import SearchRequest, SearchRun
from app.repository import ProductRepository, utc_now


def resolve_terms(db: Database, request: SearchRequest) -> list[str]:
    """Termos que a execução vai consultar.

    Termos explícitos vencem. Sem eles, saem do cadastro do produto: os termos de
    busca declarados ou, se não houver nenhum, o nome do produto.
    """
    if request.terms:
        return request.terms
    product = ProductRepository(db).get(request.product_id)
    return list(product.search_terms) if product.search_terms else [product.name]


async def run_search(
    db: Database,
    *,
    term: str,
    pages: int,
    product_id: int | None,
    collector: Collector,
) -> SearchRun:
    """Executa uma busca e persiste tudo que ela produziu, inclusive o fracasso."""
    runs = SearchRunRepository(db)
    complaints = ComplaintRepository(db)
    run_id = runs.start(term=term, source=collector.source, pages=pages, product_id=product_id)

    try:
        fetched = await collector.fetch(term, pages)
    except Exception as exc:
        # A execução fica gravada como falha, com a causa visível.
        return runs.finish(
            run_id,
            status="failed",
            notes=[f"coleta interrompida ({type(exc).__name__}: {exc})"],
        )

    parsed = parse_items(fetched.items, source=collector.source)
    now = utc_now()
    inserted = 0
    for item in parsed.items:
        complaint_id, is_new = complaints.upsert(item.complaint, now)
        inserted += is_new
        runs.record_hit(
            run_id=run_id,
            complaint_id=complaint_id,
            page=item.page,
            position=item.position,
            first_seen=is_new,
        )

    collected = len(parsed.items)
    return runs.finish(
        run_id,
        status="completed",
        found=len(fetched.items),
        collected=collected,
        inserted=inserted,
        duplicates=collected - inserted,
        failures=len(parsed.discarded) + fetched.failures,
        notes=[*fetched.notes, *parsed.notes, *parsed.discarded],
    )


async def run_searches(
    db: Database, request: SearchRequest, collector: Collector
) -> list[SearchRun]:
    """Uma execução por termo; um termo que falha não impede os demais."""
    terms = resolve_terms(db, request)
    return [
        await run_search(
            db,
            term=term,
            pages=request.pages,
            product_id=request.product_id,
            collector=collector,
        )
        for term in terms
    ]
