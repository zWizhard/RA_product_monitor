"""Pesquisa livre: etiqueta de relevância por regra, recorte por termo/período e API."""

from datetime import date, datetime

import pytest

from app.collect_repository import ComplaintRepository
from app.collector import FetchResult
from app.relevance import (
    ADVERSE,
    COMMERCIAL,
    DOUBTFUL,
    NO_SIGNAL,
    OFF_TOPIC,
    RELEVANT,
    TECHNICAL,
    classify,
)
from app.taxonomy import Category
from tests.helpers import make_complaint, make_possible
from tests.test_collect import FakeCollector, raw_item

# ------------------------------------------------------------------------ etiqueta


def test_capinha_de_silicone_fica_fora_do_assunto():
    result = classify("Capinha de silicone rasgou", "A capa do celular chegou rasgada.")
    assert result.label == OFF_TOPIC
    assert "capinha" in result.off_topic_terms


def test_protese_mamaria_e_relevante_na_area_certa():
    result = classify(
        "Prótese de silicone rompeu",
        "Oito meses depois da mamoplastia a prótese rompeu e tive infecção.",
    )
    assert result.label == RELEVANT
    assert result.area == "mamario"
    assert "mamoplastia" in result.context_terms
    assert {"rompeu", "infecção"} <= set(result.adverse_signals)


def test_area_e_outro_assunto_juntos_ficam_duvidosa():
    result = classify("Preenchimento labial", "O sérum com ácido hialurônico não chegou.")
    assert result.label == DOUBTFUL
    assert result.area == "acido_hialuronico"


def test_sem_sinal_nenhum_fica_duvidosa():
    assert classify("Silicone vazando", "Veio com defeito.").label == DOUBTFUL


def test_area_escolhida_restringe_a_etiqueta():
    text = ("Implante dentário soltou", "O dentista colocou o implante e ele soltou.")
    assert classify(*text, "dentario").label == RELEVANT
    # Com recorte, citar só outra área é outro assunto, e a palavra fica à mostra.
    other = classify(*text, "mamario")
    assert other.label == OFF_TOPIC
    assert "implante dentário" in other.off_topic_terms
    # Sem recorte e sem sinal nenhum, continua duvidosa.
    assert classify("Silicone vazando", "Veio com defeito.", "mamario").label == DOUBTFUL


def test_categoria_escolhe_a_area_mais_proxima_dentro_dela():
    text = ("Implante dentário soltou", "O dentista colocou o implante e ele soltou.")
    inside = classify(*text, category=Category.MATERIAIS_IMPLANTAVEIS)
    assert (inside.label, inside.area) == (RELEVANT, "dentario")
    assert classify(*text, category=Category.MATERIAIS_ESTETICOS).label == OFF_TOPIC


def test_termo_so_conta_como_palavra_inteira():
    # "mama" não pode casar dentro de "mamadeira".
    result = classify("Mamadeira de silicone", "O bico rasgou.", "mamario")
    assert result.context_terms == ()
    assert result.label == OFF_TOPIC


def test_triagem_por_sinais_sem_depender_de_produto():
    assert classify("Aparelho parou de funcionar", "Dá erro ao ligar.").nature == TECHNICAL
    assert classify("Pedido não chegou", "Quero reembolso.").nature == COMMERCIAL
    # Dano à pessoa pesa mais que defeito e que queixa de venda.
    both = classify("Prótese com defeito", "Rompeu e tive infecção; o frete atrasou.")
    assert both.nature == ADVERSE
    assert "defeito" in both.technical_signals and "frete" in both.commercial_signals
    assert classify("Atendimento ruim", "Ninguém responde.").nature == NO_SIGNAL
    # "Falha" da venda não é defeito do produto.
    assert classify("Falha na entrega", "O pedido atrasou.").nature == COMMERCIAL
    assert classify("Sensor falhou", "No segundo dia.").nature == TECHNICAL


def test_area_desconhecida_levanta():
    with pytest.raises(KeyError):
        classify("x", None, "inexistente")


# ---------------------------------------------------------------------- repositório


def test_termo_recorta_por_texto_e_periodo(db):
    make_complaint(db, "a", title="Prótese mamária rompeu", published_at=datetime(2026, 3, 1))
    make_complaint(db, "b", title="Prótese mamária vazou", published_at=datetime(2025, 1, 1))
    make_complaint(db, "c", title="Prótese mamária sem data")
    make_complaint(db, "d", title="Forma de bolo", published_at=datetime(2026, 3, 1))
    repo = ComplaintRepository(db)

    assert {c.external_id for c in repo.list(term="protese mamaria")} == {"a", "b", "c"}
    in_period = repo.list(term="Prótese Mamária", date_from=date(2026, 1, 1), date_to=date(2026, 3, 1))
    assert [c.external_id for c in in_period] == ["a"]
    assert repo.count_undated(term="prótese mamária") == 1


# ------------------------------------------------------------------------------ API


def test_api_une_busca_e_texto_e_ordena_por_etiqueta(client):
    # O Reclame Aqui devolve para "silicone" um texto que não cita a palavra.
    client.app.state.collector = FakeCollector(
        FetchResult(
            items=[
                raw_item("a_AAA111", title="Capinha rasgou", description="Capa do celular."),
                raw_item(
                    "b_BBB222",
                    title="Prótese mamária com problema",
                    description="Após a mamoplastia tive contratura capsular.",
                ),
            ]
        )
    )
    assert client.post("/searches", json={"terms": ["silicone"], "pages": 1}).status_code == 200

    body = client.get("/research", params={"term": "silicone"}).json()
    assert body["total"] == 2
    assert [item["relevance"] for item in body["items"]] == [RELEVANT, OFF_TOPIC]
    assert body["counts"] == {RELEVANT: 1, DOUBTFUL: 0, OFF_TOPIC: 1}
    assert body["rules_version"] == "rel-4"
    assert body["items"][0]["complaint"]["url"].startswith("https://")


def test_api_sem_termo_filtra_por_categoria(client):
    client.app.state.collector = FakeCollector(
        FetchResult(
            items=[
                raw_item("a_AAA111", title="Glicosímetro com defeito", description="Erro."),
                raw_item(
                    "b_BBB222",
                    title="Prótese mamária com problema",
                    description="Após a mamoplastia tive contratura capsular.",
                ),
            ]
        )
    )
    client.post("/searches", json={"terms": ["qualquer"], "pages": 1})

    body = client.get("/research", params={"category": "materiais_implantaveis"}).json()
    assert body["term"] is None and body["total"] == 2
    labels = {item["complaint"]["title"]: (item["relevance"], item["area"]) for item in body["items"]}
    assert labels["Prótese mamária com problema"] == (RELEVANT, "mamario")
    assert labels["Glicosímetro com defeito"][0] == OFF_TOPIC


def test_api_corta_depois_de_etiquetar(client):
    # A relevante é coletada primeiro; cortar pela coleta a deixaria de fora.
    for slug, title, description in [
        ("a_AAA111", "Prótese mamária rompeu", "Após a mamoplastia a prótese rompeu."),
        ("b_BBB222", "Capinha rasgou", "Capa do celular."),
    ]:
        client.app.state.collector = FakeCollector(
            FetchResult(items=[raw_item(slug, title=title, description=description)])
        )
        client.post("/searches", json={"terms": [slug], "pages": 1})

    body = client.get("/research", params={"area": "mamario", "limit": 1}).json()
    assert body["truncated"] is True
    assert body["total"] == 2 and body["counts"][OFF_TOPIC] == 1
    assert [item["complaint"]["title"] for item in body["items"]] == ["Prótese mamária rompeu"]


def test_api_areas_e_validacao(client):
    areas = client.get("/research/areas").json()
    assert [area["key"] for area in areas] == [
        "estetica", "glicosimetro", "mamario", "dentario", "pmma", "acido_hialuronico",
    ]
    assert areas[2]["category_label"] == "Materiais implantáveis"
    assert client.get("/research", params={"term": "x"}).status_code == 422
    assert client.get("/research", params={"term": "pmma", "area": "nada"}).status_code == 422
    mismatch = {"area": "pmma", "category": "materiais_implantaveis"}
    assert client.get("/research", params=mismatch).status_code == 422
    inverted = {"term": "pmma", "date_from": "2026-02-01", "date_to": "2026-01-01"}
    assert client.get("/research", params=inverted).status_code == 422


def test_pagina_tem_as_quatro_abas(client):
    page = client.get("/").text
    for tab in ("coleta", "reclamacoes", "dashboard", "auditoria"):
        assert f'data-tab="{tab}"' in page


def test_api_busca_ativa_separa_triagem_e_produto_identificado(client):
    db = client.app.state.db
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
    make_possible(db, "a1")  # o matching associa ao produto cadastrado
    make_complaint(db, "b1", title="Aparelho de estética quebrou", description="Parou de funcionar.")
    make_complaint(db, "c1", title="Pedido não chegou", description="Quero reembolso.")
    client.post("/matches/run", json={})

    body = client.get("/research").json()
    assert body["total"] == 3 and body["unidentified"] == 2
    assert body["natures"][TECHNICAL] == 1 and body["natures"][COMMERCIAL] == 1
    by_title = {item["complaint"]["title"]: item for item in body["items"]}
    [product] = by_title["Aparelho da Classys"]["products"]
    assert (product["name"], product["decision"], product["human"]) == (
        "Ultraformer III", "possible", False,
    )
    # Sem produto identificado continua listada, com a origem rastreável.
    unknown = by_title["Aparelho de estética quebrou"]
    assert unknown["products"] == [] and unknown["nature"] == TECHNICAL
    assert unknown["complaint"]["url"].startswith("https://")

    only_unknown = client.get("/research", params={"identified": "false"}).json()
    assert {i["complaint"]["title"] for i in only_unknown["items"]} == {
        "Aparelho de estética quebrou", "Pedido não chegou",
    }
    technical = client.get("/research", params={"nature": TECHNICAL}).json()
    assert [i["complaint"]["title"] for i in technical["items"]] == ["Aparelho de estética quebrou"]
    assert client.get("/research", params={"nature": "nada"}).status_code == 422
