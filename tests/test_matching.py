"""Testes do matching produto ↔ reclamação.

Cobrem positivos claros, negativos, casos ambíguos, idempotência do reprocessamento e
a preservação da decisão humana. Nada aqui acessa rede.
"""

from app.match_repository import MatchRepository
from app.match_service import run_matching
from app.matching import (
    MATCHING,
    MatchMethod,
    MatchStatus,
    match_product,
    status_for,
    tokenize,
)
from app.models import MatchRequest
from app.repository import ProductRepository
from tests.helpers import RA, make_complaint, make_product

# --------------------------------------------------------------- normalização


def test_tokenize_preserva_posicao_no_texto_original():
    tokens = tokenize("Ácido  hialurônico, doeu!")
    assert [t.value for t in tokens] == ["acido", "hialuronico", "doeu"]
    assert "Ácido  hialurônico"[tokens[0].start : tokens[1].end] == "Ácido  hialurônico"


def test_status_derivado_dos_limiares():
    assert status_for(MATCHING.confirm_threshold) is MatchStatus.CONFIRMED
    assert status_for(MATCHING.review_threshold) is MatchStatus.POSSIBLE
    assert status_for(MATCHING.review_threshold - 0.01) is MatchStatus.DISCARDED


# ------------------------------------------------------------ camadas do motor


def test_nome_exato_no_titulo_confirma(db):
    product = make_product(db)
    complaint = make_complaint(db, title="Queimadura com Ultraformer III na clínica")

    candidate = match_product(product, complaint)

    assert candidate.method is MatchMethod.EXACT_NAME
    assert status_for(candidate.score) is MatchStatus.CONFIRMED
    assert candidate.evidence_field == "title"
    assert "Ultraformer III" in candidate.evidence
    assert "Ultraformer III" in candidate.explanation


def test_alias_na_descricao_confirma_com_score_menor_que_o_nome(db):
    product = make_product(db)
    no_titulo = match_product(product, make_complaint(db, title="Ultraformer III ruim"))
    alias = match_product(
        product,
        make_complaint(db, "c2", description="Usaram o Ultraformer MPT e queimou."),
    )

    assert alias.method is MatchMethod.ALIAS
    assert alias.evidence_field == "description"
    assert alias.score < no_titulo.score
    assert status_for(alias.score) is MatchStatus.CONFIRMED


def test_marca_com_contexto_fica_para_revisao(db):
    product = make_product(db)
    complaint = make_complaint(
        db, title="Aparelho da Classys", description="O modelo MPT queimou minha pele."
    )

    candidate = match_product(product, complaint)

    assert candidate.method is MatchMethod.BRAND_CONTEXT
    assert status_for(candidate.score) is MatchStatus.POSSIBLE
    assert candidate.matched_term == "Classys + MPT"
    assert "Classys" in candidate.evidence and "MPT" in candidate.evidence


def test_marca_sozinha_nao_gera_match(db):
    product = make_product(db, model=None, search_terms=[], aliases=[])
    complaint = make_complaint(db, title="Atendimento da Classys foi péssimo", description=None)

    assert match_product(product, complaint) is None


def test_termo_generico_nao_confirma(db):
    product = make_product(db)
    complaint = make_complaint(db, title="Ultrassom microfocado deixou marcas")

    candidate = match_product(product, complaint)

    assert candidate.method is MatchMethod.SEARCH_TERM
    assert status_for(candidate.score) is MatchStatus.POSSIBLE
    assert "não confirma" in candidate.explanation


def test_fuzzy_cobre_grafia_errada_sem_confirmar(db):
    product = make_product(db)
    complaint = make_complaint(db, title="Fiz sessão de Ultrafomer III e queimou")

    candidate = match_product(product, complaint)

    assert candidate.method is MatchMethod.FUZZY
    assert status_for(candidate.score) is MatchStatus.POSSIBLE
    assert candidate.score <= MATCHING.fuzzy_max_score


def test_reclamacao_sem_relacao_nao_gera_match(db):
    product = make_product(db)
    complaint = make_complaint(
        db, title="Cobrança indevida no cartão", description="Estornaram só metade."
    )

    assert match_product(product, complaint) is None


def test_termo_curto_nao_casa_dentro_de_outra_palavra(db):
    product = make_product(
        db,
        name="Preenchedor AH",
        category="materiais_esteticos",
        subcategory="acido_hialuronico",
        brand=None,
        model=None,
        aliases=["AH"],
        search_terms=["acido hialuronico"],
    )
    complaint = make_complaint(db, title="Colchão de palha causou alergia", description=None)

    assert match_product(product, complaint) is None


def test_sequencia_de_tokens_exige_adjacencia(db):
    product = make_product(
        db,
        name="Ácido hialurônico Restylane",
        category="materiais_esteticos",
        subcategory="acido_hialuronico",
        brand=None,
        model=None,
        aliases=[],
        search_terms=[],
    )
    complaint = make_complaint(
        db,
        title="Ácido usado na clínica",
        description="Depois aplicaram hialurônico Restylane em outra sessão.",
    )

    candidate = match_product(product, complaint)

    assert candidate is None or candidate.method is MatchMethod.FUZZY


# ------------------------------------------------- persistência e idempotência


def test_run_grava_match_com_evidencia_e_metodo(db):
    make_product(db)
    make_complaint(db, title="Queimadura com Ultraformer III")

    result = run_matching(db, MatchRequest())

    assert (result.created, result.matched, result.confirmed) == (1, 1, 1)
    saved = MatchRepository(db).list()
    assert len(saved) == 1
    match = saved[0]
    assert match.method is MatchMethod.EXACT_NAME
    assert match.matcher_version == MATCHING.version
    assert match.evidence and match.explanation
    assert match.decision is MatchStatus.CONFIRMED
    assert match.reviewed_status is None


def test_reprocessar_nao_duplica_nem_altera(db):
    make_product(db)
    make_complaint(db, title="Queimadura com Ultraformer III")

    first = run_matching(db, MatchRequest())
    before = MatchRepository(db).list()[0]
    second = run_matching(db, MatchRequest())
    after = MatchRepository(db).list()[0]

    assert (first.created, first.updated, first.unchanged) == (1, 0, 0)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 1)
    assert len(MatchRepository(db).list()) == 1
    assert after.id == before.id
    assert after.updated_at == before.updated_at


def test_reprocessar_atualiza_quando_o_cadastro_muda(db):
    from app.models import ProductUpdate

    product = make_product(db, aliases=[], search_terms=[])
    make_complaint(db, title="Problema com o HIFU da clínica", description=None)
    assert run_matching(db, MatchRequest()).matched == 0

    ProductRepository(db).update(product.id, ProductUpdate(aliases=["HIFU"]))
    result = run_matching(db, MatchRequest())
    assert (result.created, result.matched) == (1, 1)

    ProductRepository(db).update(product.id, ProductUpdate(search_terms=["HIFU"], aliases=[]))
    updated = run_matching(db, MatchRequest())
    assert (updated.created, updated.updated, updated.unchanged) == (0, 1, 0)
    match = MatchRepository(db).list()[0]
    assert match.method is MatchMethod.SEARCH_TERM


def test_revisao_humana_sobrevive_ao_reprocessamento(db):
    make_product(db)
    make_complaint(db, title="Queimadura com Ultraformer III")
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)
    match_id = repository.list()[0].id

    reviewed = repository.review(
        match_id, status=MatchStatus.DISCARDED, reviewed_by="analista", note="outro aparelho"
    )
    assert reviewed.decision is MatchStatus.DISCARDED
    assert reviewed.status is MatchStatus.CONFIRMED

    run_matching(db, MatchRequest())
    after = repository.get(match_id)
    assert after.reviewed_status is MatchStatus.DISCARDED
    assert after.reviewed_by == "analista"
    assert after.review_note == "outro aparelho"
    assert after.decision is MatchStatus.DISCARDED


def test_escopo_por_produto_e_por_reclamacao(db):
    product = make_product(db)
    other = make_product(db, name="Glicosímetro Accu-Chek", category="diagnostico_in_vitro",
                         subcategory="glicosimetro", brand="Roche", model=None,
                         aliases=[], search_terms=[])
    first = make_complaint(db, "c1", title="Queimadura com Ultraformer III")
    make_complaint(db, "c2", title="Glicosímetro Accu-Chek marcando errado")

    scoped = run_matching(db, MatchRequest(product_id=product.id, complaint_id=first.id))
    assert scoped.pairs_evaluated == 1
    assert scoped.matched == 1
    assert [m.product_id for m in MatchRepository(db).list()] == [product.id]

    run_matching(db, MatchRequest())
    assert {m.product_id for m in MatchRepository(db).list()} == {product.id, other.id}


def test_produto_inativo_fica_fora_do_reprocessamento_geral(db):
    product = make_product(db)
    make_complaint(db, title="Queimadura com Ultraformer III")
    ProductRepository(db).set_active(product.id, False)

    assert run_matching(db, MatchRequest()).matched == 0
    assert run_matching(db, MatchRequest(product_id=product.id)).matched == 1


# ------------------------------------------------------------------------ API


def test_api_fluxo_completo_de_matching(client):
    client.post(
        "/products",
        json={
            "name": "Ultraformer III",
            "category": "equipamentos",
            "subcategory": "estetica",
            "brand": "Classys",
        },
    ).raise_for_status()
    db = client.app.state.db
    make_complaint(db, title="Queimadura com Ultraformer III")

    run = client.post("/matches/run", json={})
    assert run.status_code == 200
    assert run.json()["created"] == 1

    listed = client.get("/matches", params={"status": "confirmed"}).json()
    assert len(listed) == 1
    match = listed[0]
    assert match["explanation"] and match["evidence"]
    assert match["decision"] == "confirmed"

    detail = client.get(f"/matches/{match['id']}").json()
    assert detail["method"] == "exact_name"
    assert detail["matched_term"] == "Ultraformer III"

    review = client.post(
        f"/matches/{match['id']}/review",
        json={"status": "discarded", "reviewed_by": "analista", "note": "equipamento diferente"},
    ).json()
    assert review["decision"] == "discarded"
    assert review["status"] == "confirmed"
    assert client.get("/matches", params={"status": "discarded"}).json()[0]["id"] == match["id"]
    assert client.get("/matches", params={"status": "confirmed"}).json() == []


def test_api_match_inexistente_responde_404(client):
    assert client.get("/matches/999").status_code == 404
    assert (
        client.post(
            "/matches/999/review", json={"status": "confirmed", "reviewed_by": "analista"}
        ).status_code
        == 404
    )


# ------------------------------------------ evidência perdida e escopo truncado


def test_match_que_perde_evidencia_fica_datado_sem_ser_apagado(db):
    from app.models import ProductUpdate

    product = make_product(db, name="Aparelho HIFU Pro", aliases=[], search_terms=[])
    make_complaint(db, title="Queimadura com Aparelho HIFU Pro", description=None)
    assert run_matching(db, MatchRequest()).created == 1
    before = MatchRepository(db).list()[0]

    ProductRepository(db).update(product.id, ProductUpdate(name="Outro Equipamento"))
    result = run_matching(db, MatchRequest())

    assert (result.matched, result.stale) == (0, 1)
    after = MatchRepository(db).get(before.id)
    assert after.stale_since is not None
    # A evidência que sustentou a associação continua gravada.
    assert after.evidence == before.evidence
    assert after.matched_term == before.matched_term
    assert [m.id for m in MatchRepository(db).list(stale=True)] == [before.id]
    assert MatchRepository(db).list(stale=False) == []


def test_evidencia_reencontrada_deixa_de_ser_obsoleta(db):
    from app.models import ProductUpdate

    product = make_product(db, name="Aparelho HIFU Pro", aliases=[], search_terms=[])
    make_complaint(db, title="Queimadura com Aparelho HIFU Pro", description=None)
    run_matching(db, MatchRequest())
    ProductRepository(db).update(product.id, ProductUpdate(name="Outro Equipamento"))
    assert run_matching(db, MatchRequest()).stale == 1

    ProductRepository(db).update(product.id, ProductUpdate(name="Aparelho HIFU Pro"))
    result = run_matching(db, MatchRequest())

    assert (result.updated, result.stale) == (1, 0)
    assert MatchRepository(db).list()[0].stale_since is None


def test_datar_como_obsoleto_nao_toca_na_revisao_humana(db):
    from app.models import ProductUpdate

    product = make_product(db, name="Aparelho HIFU Pro", aliases=[], search_terms=[])
    make_complaint(db, title="Queimadura com Aparelho HIFU Pro", description=None)
    run_matching(db, MatchRequest())
    repository = MatchRepository(db)
    match_id = repository.list()[0].id
    repository.review(
        match_id, status=MatchStatus.CONFIRMED, reviewed_by="analista", note="conferido"
    )

    ProductRepository(db).update(product.id, ProductUpdate(name="Outro Equipamento"))
    run_matching(db, MatchRequest())

    after = repository.get(match_id)
    assert after.stale_since is not None
    assert after.decision is MatchStatus.CONFIRMED
    assert after.reviewed_by == "analista"
    assert after.review_note == "conferido"


def test_obsoleto_so_alcanca_pares_dentro_do_escopo(db):
    from app.models import ProductUpdate

    product = make_product(db, name="Aparelho HIFU Pro", aliases=[], search_terms=[])
    first = make_complaint(db, "c1", title="Queimadura com Aparelho HIFU Pro", description=None)
    second = make_complaint(db, "c2", title="Dor após Aparelho HIFU Pro", description=None)
    assert run_matching(db, MatchRequest()).created == 2

    # O cadastro muda, mas o reprocessamento é restrito a uma reclamação.
    ProductRepository(db).update(product.id, ProductUpdate(name="Outro Equipamento"))
    result = run_matching(db, MatchRequest(complaint_id=first.id))

    assert result.stale == 1
    repository = MatchRepository(db)
    assert [m.complaint_id for m in repository.list(stale=True)] == [first.id]
    assert [m.complaint_id for m in repository.list(stale=False)] == [second.id]


def test_filtros_de_revisao_e_paginacao_do_escopo(db):
    make_product(db)
    for index in range(3):
        make_complaint(db, f"c{index}", title=f"Queimadura com Ultraformer III {index}")

    primeira = run_matching(db, MatchRequest(limit=2))
    assert (primeira.complaints_evaluated, primeira.created, primeira.truncated) == (2, 2, True)
    segunda = run_matching(db, MatchRequest(limit=2, offset=2))
    assert (segunda.complaints_evaluated, segunda.created, segunda.truncated) == (1, 1, False)

    repository = MatchRepository(db)
    assert len(repository.list()) == 3
    repository.review(
        repository.list()[0].id, status=MatchStatus.DISCARDED, reviewed_by="ana", note=None
    )
    assert len(repository.list(reviewed=True)) == 1
    assert len(repository.list(reviewed=False)) == 2


def test_api_expoe_obsoletos_e_truncamento(client):
    client.post(
        "/products",
        json={"name": "Ultraformer III", "category": "equipamentos", "subcategory": "estetica"},
    ).raise_for_status()
    db = client.app.state.db
    make_complaint(db, title="Queimadura com Ultraformer III")
    assert client.post("/matches/run", json={}).json()["truncated"] is False

    product_id = client.get("/products").json()[0]["id"]
    client.patch(f"/products/{product_id}", json={"name": "Outro Equipamento"}).raise_for_status()
    run = client.post("/matches/run", json={}).json()
    assert run["stale"] == 1

    obsoletos = client.get("/matches", params={"stale": True}).json()
    assert len(obsoletos) == 1
    assert obsoletos[0]["stale_since"] is not None
    assert client.get("/matches", params={"stale": False}).json() == []
