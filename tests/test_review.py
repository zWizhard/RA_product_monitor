"""Testes da validação humana dos matches.

Cobrem os estados (confirmado, pendente, descartado), as transições, a persistência da
decisão humana e a fila de revisão. A decisão automática e a fonte original nunca são
alteradas pela revisão.
"""

import pytest

from app.collect_repository import ComplaintRepository
from app.db import Database
from app.match_repository import MatchRepository
from app.match_service import run_matching
from app.matching import MatchStatus
from app.models import MatchRequest, ProductUpdate
from app.repository import ProductRepository
from tests.helpers import RA, make_complaint, make_possible, make_product

# -------------------------------------------------------------------- estados


def test_match_possivel_nasce_pendente(db):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())

    match = MatchRepository(db).list()[0]

    assert match.status is MatchStatus.POSSIBLE
    assert match.reviewed_status is None
    assert match.decision is MatchStatus.POSSIBLE
    assert match.pending is True


def test_match_confirmado_pelo_motor_nao_e_pendente(db):
    make_product(db)
    make_complaint(db, title="Queimadura com Ultraformer III")
    run_matching(db, MatchRequest())

    assert MatchRepository(db).list()[0].pending is False


def test_match_obsoleto_sai_da_fila_mesmo_sem_decisao(db):
    product = make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    ProductRepository(db).update(product.id, ProductUpdate(brand="Outra Marca"))
    run_matching(db, MatchRequest())

    match = MatchRepository(db).list()[0]

    assert match.stale_since is not None
    assert match.decision is MatchStatus.POSSIBLE
    assert match.pending is False
    assert MatchRepository(db).queue() == []


# ----------------------------------------------------------------- transições


@pytest.mark.parametrize("decision", [MatchStatus.CONFIRMED, MatchStatus.DISCARDED])
def test_decisao_humana_tira_da_fila_sem_apagar_a_automatica(db, decision):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)
    before = repository.list()[0]

    reviewed = repository.review(
        before.id, status=decision, reviewed_by="analista", note="conferido"
    )

    assert reviewed.decision is decision
    assert reviewed.pending is False
    assert reviewed.reviewed_by == "analista"
    assert reviewed.reviewed_at is not None
    assert reviewed.review_note == "conferido"
    # A decisão automática e a evidência que a sustentou ficam intactas.
    assert reviewed.status is before.status
    assert reviewed.score == before.score
    assert reviewed.method is before.method
    assert reviewed.evidence == before.evidence
    assert reviewed.explanation == before.explanation


def test_analista_pode_manter_em_duvida_e_o_par_continua_na_fila(db):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)
    match_id = repository.list()[0].id

    reviewed = repository.review(
        match_id, status=MatchStatus.POSSIBLE, reviewed_by="analista", note="falta o modelo"
    )

    assert reviewed.reviewed_status is MatchStatus.POSSIBLE
    assert reviewed.pending is True
    assert [item.match.id for item in repository.queue()] == [match_id]


def test_revisao_seguinte_substitui_a_anterior(db):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)
    match_id = repository.list()[0].id

    first = repository.review(
        match_id, status=MatchStatus.CONFIRMED, reviewed_by="ana", note="parece o mesmo"
    )
    second = repository.review(
        match_id, status=MatchStatus.DISCARDED, reviewed_by="bruno", note="outro aparelho"
    )

    assert second.decision is MatchStatus.DISCARDED
    assert second.reviewed_by == "bruno"
    assert second.review_note == "outro aparelho"
    assert second.reviewed_at >= first.reviewed_at
    assert second.status is MatchStatus.POSSIBLE


def test_decisao_humana_persiste_no_banco(db):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    match_id = MatchRepository(db).list()[0].id
    MatchRepository(db).review(
        match_id, status=MatchStatus.CONFIRMED, reviewed_by="analista", note="confirmado"
    )
    path = db.path
    db.close()

    reopened = Database(path)
    reopened.connect()
    try:
        match = MatchRepository(reopened).get(match_id)
    finally:
        reopened.close()

    assert match.reviewed_status is MatchStatus.CONFIRMED
    assert match.reviewed_by == "analista"
    assert match.reviewed_at is not None
    assert match.review_note == "confirmado"
    assert match.status is MatchStatus.POSSIBLE


# -------------------------------------------------------------- fila e filtros


def test_fila_traz_produto_e_reclamacao_junto_da_evidencia(db):
    product = make_product(db)
    complaint = make_possible(db)
    run_matching(db, MatchRequest())

    item = MatchRepository(db).queue()[0]

    assert item.product.id == product.id
    assert item.product.name == "Ultraformer III"
    assert item.product.brand == "Classys"
    assert item.complaint.id == complaint.id
    assert item.complaint.title == "Aparelho da Classys"
    assert item.complaint.url == complaint.url
    assert item.complaint.description == "O modelo MPT queimou minha pele."
    assert item.match.score > 0
    assert item.match.evidence and item.match.explanation


def test_fila_ordena_por_score_e_filtra_produto_e_categoria(db):
    equipamento = make_product(db)
    gel = make_product(
        db,
        name="Gel Preenchedor AH",
        category="materiais_esteticos",
        subcategory="acido_hialuronico",
        brand="Allergan",
        model="Volume",
        aliases=[],
        search_terms=[],
    )
    make_possible(db, "c1")
    make_complaint(
        db,
        "c2",
        title="Produto da Allergan",
        description="O Volume deixou nódulos.",
    )
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)

    todos = repository.queue()
    scores = [item.match.score for item in todos]

    assert len(todos) == 2
    assert scores == sorted(scores, reverse=True)
    assert [i.product.id for i in repository.queue(product_id=gel.id)] == [gel.id]
    assert [i.product.id for i in repository.queue(category="equipamentos")] == [
        equipamento.id
    ]


def test_fila_pagina_sem_repetir_nem_perder_par(db):
    make_product(db)
    for slug in ("c1", "c2", "c3"):
        make_possible(db, slug)
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)

    todos = [item.match.id for item in repository.queue()]
    pagina = [item.match.id for item in repository.queue(limit=2)]
    resto = [item.match.id for item in repository.queue(limit=2, offset=2)]

    assert len(todos) == 3
    assert pagina + resto == todos


def test_fila_esconde_o_que_foi_decidido_e_o_reexame_recupera(db):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)
    match_id = repository.queue()[0].match.id

    repository.review(
        match_id, status=MatchStatus.DISCARDED, reviewed_by="analista", note=None
    )

    assert repository.queue() == []
    assert [i.match.id for i in repository.queue(pending=False)] == [match_id]
    assert [i.match.id for i in repository.queue(pending=None)] == [match_id]
    assert [m.id for m in repository.list(pending=False)] == [match_id]
    assert repository.list(pending=True) == []


# ------------------------------------------------------- fonte original intacta


def test_revisao_nao_altera_a_reclamacao_coletada(db):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)
    match = repository.list()[0]
    before = ComplaintRepository(db).get(match.complaint_id)

    repository.review(
        match.id, status=MatchStatus.DISCARDED, reviewed_by="analista", note="não é o produto"
    )

    assert ComplaintRepository(db).get(match.complaint_id) == before


def test_api_de_reclamacoes_e_somente_leitura(client):
    paths = client.get("/openapi.json").json()["paths"]
    reclamacoes = {
        path: set(operations) for path, operations in paths.items() if "/complaints" in path
    }

    assert reclamacoes
    assert all(operations == {"get"} for operations in reclamacoes.values())


# ------------------------------------------------------------------------ API


def test_api_fluxo_de_validacao_humana(client):
    client.post(
        "/products",
        json={
            "name": "Ultraformer III",
            "category": "equipamentos",
            "subcategory": "estetica",
            "brand": "Classys",
            "model": "MPT",
        },
    )
    make_possible(client.app.state.db)
    assert client.post("/matches/run", json={}).json()["possible"] == 1

    queue = client.get("/matches/queue").json()
    assert len(queue) == 1
    item = queue[0]
    assert item["match"]["pending"] is True
    assert item["product"]["name"] == "Ultraformer III"
    assert item["complaint"]["title"] == "Aparelho da Classys"
    assert item["complaint"]["url"].startswith(RA)

    match_id = item["match"]["id"]
    review = client.post(
        f"/matches/{match_id}/review",
        json={
            "status": "confirmed",
            "reviewed_by": "analista",
            "note": "é o mesmo equipamento",
        },
    ).json()

    assert review["decision"] == "confirmed"
    assert review["status"] == "possible"
    assert review["pending"] is False
    assert client.get("/matches/queue").json() == []
    assert client.get("/matches", params={"pending": True}).json() == []
    assert [m["id"] for m in client.get("/matches", params={"pending": False}).json()] == [
        match_id
    ]
    # O reexame do que já foi decidido continua acessível pela mesma rota.
    for scope in ("decided", "all"):
        decididos = client.get("/matches/queue", params={"scope": scope}).json()
        assert [i["match"]["id"] for i in decididos] == [match_id]


def test_api_fila_rejeita_filtro_fora_do_vocabulario(client):
    assert client.get("/matches/queue", params={"category": "inexistente"}).status_code == 422
    assert client.get("/matches/queue", params={"scope": "inexistente"}).status_code == 422
