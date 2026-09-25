"""
Orquestração do matching: produtos x reclamações -> candidatos -> matches gravados.

"Foi encontrado pela busca" não é "é o produto": a associação só existe depois que
este passo encontra evidência textual no conteúdo da reclamação.

Reprocessar é seguro e esperado — depois de editar termos de um produto, por exemplo.
O mesmo par produz o mesmo resultado, sem linha duplicada e sem apagar revisão humana.
"""

from __future__ import annotations

from app.collect_repository import ComplaintRepository
from app.db import Database
from app.match_repository import MatchRepository
from app.matching import (
    MATCHING,
    MatchingConfig,
    MatchStatus,
    build_catalog,
    decide,
    match_complaint,
)
from app.models import Complaint, MatchRequest, MatchRunResult, Product
from app.repository import ProductRepository, utc_now

# Teto de produtos avaliados em uma execução sem filtro. O cadastro é um escopo de
# vigilância definido a priori; se passar disto, a execução deve ser fatiada por produto.
_MAX_PRODUCTS = 500


def _products(db: Database, request: MatchRequest) -> tuple[list[Product], set[int]]:
    """Produtos avaliados e, entre eles, quais têm o match gravado.

    Sem filtro, os ativos — todos gravados. Com `product_id`, o produto pedido (mesmo
    inativo) é o único gravado, mas os demais ativos continuam sendo avaliados: a
    arbitragem por especificidade compara candidatos entre si, e sem os vizinhos o
    candidato menos específico voltaria a confirmar sozinho num reprocessamento
    fatiado por produto.
    """
    repository = ProductRepository(db)
    active = repository.list(active=True, limit=_MAX_PRODUCTS)
    if request.product_id is None:
        return active, {product.id for product in active}
    target = repository.get(request.product_id)
    others = [product for product in active if product.id != target.id]
    return [target, *others], {target.id}


def _complaints(db: Database, request: MatchRequest) -> list[Complaint]:
    repository = ComplaintRepository(db)
    if request.complaint_id is not None:
        return [repository.get(request.complaint_id)]
    return repository.list(
        search_run_id=request.search_run_id, limit=request.limit, offset=request.offset
    )


def run_matching(
    db: Database, request: MatchRequest, config: MatchingConfig = MATCHING
) -> MatchRunResult:
    """Avalia o escopo pedido e persiste os candidatos encontrados."""
    products, persisted = _products(db, request)
    complaints = _complaints(db, request)
    matches = MatchRepository(db)
    now = utc_now()
    # O vocabulário de marcas do escopo é o que permite ler a empresa da página como
    # atribuição de marca, em vez de como texto qualquer.
    catalog = build_catalog(products)

    outcomes = {"created": 0, "updated": 0, "unchanged": 0}
    decisions = {MatchStatus.CONFIRMED: 0, MatchStatus.POSSIBLE: 0}
    matched_pairs: set[tuple[int, int]] = set()
    # Par que uma regra de arbitragem tirou de campo, com a regra que o tirou.
    suppressed: dict[tuple[int, int], str] = {}

    for complaint in complaints:
        # Preparo, confronto e arbitragem são o motor: `match_complaint` é o mesmo
        # caminho que `app.evaluation` mede, para que medida e produção não divirjam.
        for product, candidate in match_complaint(complaint, products, catalog, config):
            product_id = product.id
            if product_id not in persisted:
                continue
            if candidate.suppressed_by is not None:
                # Deixou de ser candidato: o par segue o mesmo caminho de quem não tem
                # evidência — sem linha nova, e o que já existia fica datado como obsoleto,
                # registrando qual regra o suprimiu.
                suppressed[(product_id, complaint.id)] = (
                    f"suprimido por {candidate.suppressed_by} ({config.version})"
                )
                continue
            status = decide(candidate, config)
            if status is MatchStatus.DISCARDED:
                # Abaixo do piso de revisão: não há evidência suficiente nem para
                # ocupar a fila de validação humana.
                continue
            outcome = matches.save(
                product_id=product_id,
                complaint_id=complaint.id,
                candidate=candidate,
                status=status,
                matcher_version=config.version,
                now=now,
            )
            outcomes[outcome] += 1
            decisions[status] += 1
            matched_pairs.add((product_id, complaint.id))

    # Par que tinha match e não reencontrou evidência neste escopo passa a datado como
    # obsoleto — nunca apagado, para não perder a evidência que sustentou a associação.
    complaint_ids = {complaint.id for complaint in complaints}
    previous = matches.pairs_for_products(sorted(persisted))
    perdidos = {pair for pair in previous if pair[1] in complaint_ids} - matched_pairs
    stale = {
        pair: suppressed.get(pair, f"sem evidência ({config.version})") for pair in perdidos
    }

    # O escopo pode ter parado em um teto: quem chamou precisa saber que ainda há o que
    # reprocessar, continuando por `offset`. Fatiar por produto reduz o que é gravado,
    # não o custo do confronto: a arbitragem exige avaliar os vizinhos de qualquer modo.
    truncated = (request.complaint_id is None and len(complaints) >= request.limit) or (
        request.product_id is None and len(products) >= _MAX_PRODUCTS
    )

    return MatchRunResult(
        products_evaluated=len(persisted),
        complaints_evaluated=len(complaints),
        pairs_evaluated=len(persisted) * len(complaints),
        matched=sum(outcomes.values()),
        stale=matches.mark_stale(stale, now),
        truncated=truncated,
        created=outcomes["created"],
        updated=outcomes["updated"],
        unchanged=outcomes["unchanged"],
        confirmed=decisions[MatchStatus.CONFIRMED],
        possible=decisions[MatchStatus.POSSIBLE],
        matcher_version=config.version,
    )
