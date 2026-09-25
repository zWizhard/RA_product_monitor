"""Testes das métricas do dashboard.

Cobrem o que a Fase 6 exige: contadores da operação, produtos mais citados, série
temporal e variação contra o período anterior — todos contados pelo banco. Verificam
também o que as métricas *não* devem fazer: contar par obsoleto como vigente, ignorar
decisão humana, posicionar reclamação sem data numa série, ou afirmar tendência que o
teste não sustenta.
"""

from datetime import date, datetime, timedelta

import pytest

from app.analytics import MIN_TREND_SAMPLE, AnalyticsRepository, DashboardFilters, compare_counts
from app.match_repository import MatchRepository
from app.match_service import run_matching
from app.matching import MatchStatus
from app.models import Granularity, MatchRequest, ProductUpdate
from app.repository import ProductRepository, utc_now
from tests.helpers import make_complaint, make_possible, make_product

ALL = DashboardFilters()


def metrics(db) -> AnalyticsRepository:
    return AnalyticsRepository(db)


def seed_confirmed(db, slug="conf"):
    """Reclamação que cita o modelo: o motor confirma sozinho."""
    return make_complaint(db, slug, title="Queimadura com Ultraformer III")


def publish(db, slug, days_ago):
    """Reclamação publicada `days_ago` dias antes de hoje (UTC)."""
    return make_complaint(
        db,
        slug,
        title="Reclamação de período",
        published_at=day_before(days_ago),
    )


def day_before(days_ago):
    return datetime.combine(utc_now().date() - timedelta(days=days_ago), datetime.min.time())


def searched(db, term, days_ago):
    """Execução de busca datada no passado.

    `SearchRunRepository.start` sempre data no instante atual; o teste precisa de
    buscas em janelas passadas, por isso grava a linha direto.
    """
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO search_run (term, term_normalized, source, status,
                pages_requested, started_at)
            VALUES (?, ?, 'reclame_aqui', 'completed', 1, ?)
            """,
            [term, term, day_before(days_ago)],
        )


def cover(db, days=90):
    """Coleta nas duas janelas: é o que autoriza comparar volume entre elas.

    A busca atual é de hoje porque a janela termina na última coleta: coleta velha
    encurta a janela em vez de cobri-la.
    """
    searched(db, "atual", 0)
    searched(db, "anterior", days + 10)


# ----------------------------------------------------------------- contadores


def test_overview_conta_produtos_reclamacoes_matches_e_pendencias(db):
    make_product(db)
    seed_confirmed(db)
    make_possible(db, "poss")
    make_complaint(db, "solta", title="Cobrança indevida na fatura", description="Sem produto.")
    run_matching(db, MatchRequest())

    overview = metrics(db).overview(ALL)

    assert (overview.products.total, overview.products.active) == (1, 1)
    assert overview.complaints.total == 3
    # A reclamação sem correspondência continua contada: ela é resultado da coleta.
    assert overview.complaints.linked == 2
    assert overview.matches.current == 2
    assert (overview.matches.confirmed, overview.matches.possible) == (1, 1)
    assert overview.matches.pending == 1
    assert overview.matches.reviewed == 0


def test_overview_reporta_categorias_e_situacoes_da_fonte(db):
    make_product(db)
    seed_confirmed(db, "a")
    make_complaint(db, "b", title="Queimadura com Ultraformer III", status="Resolvido")
    run_matching(db, MatchRequest())

    overview = metrics(db).overview(ALL)

    assert [(c.category, c.subcategory, c.total) for c in overview.products.by_category] == [
        ("equipamentos", "estetica", 1)
    ]
    # `status` nulo é ausência de informação na fonte, não uma categoria inventada.
    assert {(row.status, row.complaints) for row in overview.complaints.by_status} == {
        (None, 1),
        ("Resolvido", 1),
    }


def test_filtro_de_categoria_restringe_produtos_e_reclamacoes(db):
    make_product(db)
    make_product(
        db,
        name="Accu-Chek Guide",
        category="diagnostico_in_vitro",
        subcategory="glicosimetro",
        brand="Roche",
        model=None,
        aliases=[],
        search_terms=[],
    )
    seed_confirmed(db, "estetica")
    make_complaint(db, "glico", title="Accu-Chek Guide com erro de leitura")
    run_matching(db, MatchRequest())

    overview = metrics(db).overview(DashboardFilters(category="diagnostico_in_vitro"))

    assert overview.products.total == 1
    assert overview.complaints.total == 1
    assert overview.complaints.linked == 1
    assert overview.matches.current == 1


def test_recorte_temporal_usa_a_publicacao_e_conta_sem_data_a_parte(db):
    make_product(db)
    seed_confirmed(db, "sem_data")
    make_complaint(
        db,
        "dentro",
        title="Queimadura com Ultraformer III",
        published_at=datetime(2025, 3, 10, 12, 0),
    )
    make_complaint(
        db,
        "fora",
        title="Queimadura com Ultraformer III",
        published_at=datetime(2024, 3, 10, 12, 0),
    )
    run_matching(db, MatchRequest())

    overview = metrics(db).overview(
        DashboardFilters(date_from=date(2025, 1, 1), date_to=date(2025, 12, 31))
    )

    assert overview.complaints.total == 1
    # A reclamação sem data legível não cabe em recorte nenhum, e é justamente isso
    # que a série temporal não mostra — por isso vem sempre.
    assert overview.complaints.undated == 1
    assert overview.matches.current == 1


def test_match_obsoleto_conta_como_obsoleto_e_nao_como_vigente(db):
    product = make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    ProductRepository(db).update(product.id, ProductUpdate(brand="Outra Marca"))
    run_matching(db, MatchRequest())

    overview = metrics(db).overview(ALL)

    assert (overview.matches.total, overview.matches.current, overview.matches.stale) == (1, 0, 1)
    assert overview.matches.pending == 0
    assert overview.complaints.linked == 0
    assert metrics(db).top_products(ALL) == []


@pytest.mark.parametrize(
    ("decision", "confirmed", "discarded", "linked"),
    [(MatchStatus.CONFIRMED, 1, 0, 1), (MatchStatus.DISCARDED, 0, 1, 0)],
)
def test_metricas_seguem_a_decisao_humana(db, decision, confirmed, discarded, linked):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    match = MatchRepository(db).list()[0]
    MatchRepository(db).review(match.id, status=decision, reviewed_by="analista", note=None)

    overview = metrics(db).overview(ALL)

    assert overview.matches.confirmed == confirmed
    assert overview.matches.discarded == discarded
    assert overview.matches.possible == 0
    assert overview.matches.pending == 0
    assert overview.matches.reviewed == 1
    assert overview.complaints.linked == linked


def test_overview_conta_buscas_pela_data_de_execucao(db):
    vazio = metrics(db).overview(ALL)
    assert (vazio.searches.runs, vazio.searches.inserted) == (0, 0)
    assert vazio.searches.last_run_at is None

    searched(db, "antiga", 400)
    searched(db, "recente", 10)

    todas = metrics(db).overview(ALL).searches
    recorte = metrics(db).overview(
        DashboardFilters(date_from=utc_now().date() - timedelta(days=30))
    ).searches

    assert todas.runs == 2
    assert todas.completed == 2
    # O recorte de datas das buscas é a execução, não a publicação da reclamação.
    assert recorte.runs == 1


def test_busca_avulsa_sai_do_escopo_quando_ha_filtro_de_produto(db):
    product = make_product(db)
    searched(db, "avulsa", 1)

    assert metrics(db).overview(ALL).searches.runs == 1
    assert metrics(db).overview(DashboardFilters(product_id=product.id)).searches.runs == 0


# ------------------------------------------------------------ produtos citados


def test_top_products_ordena_por_reclamacoes_distintas_e_exclui_quem_nao_foi_citado(db):
    make_product(db)
    make_product(db, name="Hifu Sem Citação", brand="Outra", model=None, aliases=[], search_terms=[])
    seed_confirmed(db, "a")
    make_complaint(db, "b", title="Ultraformer III falhou")
    make_possible(db, "c")
    run_matching(db, MatchRequest())

    top = metrics(db).top_products(ALL)

    assert [(row.name, row.complaints, row.confirmed, row.possible, row.pending) for row in top] == [
        ("Ultraformer III", 3, 2, 1, 1)
    ]


# -------------------------------------------------------------- série temporal


def test_timeline_preenche_passos_vazios_e_deixa_sem_data_fora_da_serie(db):
    make_product(db)
    make_complaint(
        db, "jan", title="Ultraformer III falhou", published_at=datetime(2025, 1, 15, 9, 0)
    )
    make_complaint(
        db, "mar", title="Ultraformer III falhou", published_at=datetime(2025, 3, 2, 9, 0)
    )
    make_complaint(db, "sem_data", title="Ultraformer III falhou")
    run_matching(db, MatchRequest())

    timeline = metrics(db).timeline(ALL, granularity=Granularity.MONTH)

    assert [(str(b.period_start), b.complaints) for b in timeline.buckets] == [
        ("2025-01-01", 1),
        ("2025-02-01", 0),
        ("2025-03-01", 1),
    ]
    assert timeline.undated == 1


def test_timeline_semanal_preenche_as_semanas_vazias_a_partir_da_segunda(db):
    """O passo semanal do banco começa na segunda; o preenchimento soma 7 dias.

    Se as duas convenções divergissem, a chave calculada não encontraria a contada e
    a semana sumiria da série com zero no lugar — silenciosamente.
    """
    make_product(db)
    # Quarta-feira: o passo dela começa na segunda anterior, 2024-12-30.
    make_complaint(
        db, "quarta", title="Ultraformer III falhou", published_at=datetime(2025, 1, 1, 9, 0)
    )
    make_complaint(
        db, "segunda", title="Ultraformer III falhou", published_at=datetime(2025, 1, 20, 9, 0)
    )
    run_matching(db, MatchRequest())

    timeline = metrics(db).timeline(ALL, granularity=Granularity.WEEK)

    assert [(str(b.period_start), b.complaints) for b in timeline.buckets] == [
        ("2024-12-30", 1),
        ("2025-01-06", 0),
        ("2025-01-13", 0),
        ("2025-01-20", 1),
    ]
    assert {b.period_start.weekday() for b in timeline.buckets} == {0}
    assert sum(b.complaints for b in timeline.buckets) == 2


def test_timeline_diaria_preenche_os_dias_vazios(db):
    make_product(db)
    make_complaint(
        db, "dia1", title="Ultraformer III falhou", published_at=datetime(2025, 3, 1, 9, 0)
    )
    make_complaint(
        db, "dia4", title="Ultraformer III falhou", published_at=datetime(2025, 3, 4, 23, 30)
    )
    run_matching(db, MatchRequest())

    timeline = metrics(db).timeline(ALL, granularity=Granularity.DAY)

    assert [(str(b.period_start), b.complaints) for b in timeline.buckets] == [
        ("2025-03-01", 1),
        ("2025-03-02", 0),
        ("2025-03-03", 0),
        ("2025-03-04", 1),
    ]


def test_timeline_separa_confirmadas_das_demais(db):
    make_product(db)
    make_complaint(
        db, "conf", title="Ultraformer III falhou", published_at=datetime(2025, 5, 1, 9, 0)
    )
    make_possible(db, "poss")
    run_matching(db, MatchRequest())
    # A possível recebe data no mesmo mês, para as duas séries caírem no mesmo passo.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE complaint SET published_at = ? WHERE external_id = 'poss'",
            [datetime(2025, 5, 20, 9, 0)],
        )

    timeline = metrics(db).timeline(ALL, granularity=Granularity.MONTH)

    assert [(b.complaints, b.confirmed) for b in timeline.buckets] == [(2, 1)]


def test_timeline_vazia_quando_nao_ha_publicacao_no_recorte(db):
    make_product(db)
    make_complaint(db, "sem_data", title="Ultraformer III falhou")
    run_matching(db, MatchRequest())

    timeline = metrics(db).timeline(ALL)

    assert timeline.buckets == []
    assert timeline.undated == 1


# ---------------------------------------------------------------- variação


@pytest.mark.parametrize(
    ("current", "previous", "significant"),
    [(0, 0, False), (4, 1, False), (10, 8, False), (20, 2, True), (2, 20, True)],
)
def test_compare_counts_so_conclui_com_amostra_e_diferenca(current, previous, significant):
    p_value, is_significant, note = compare_counts(current, previous)

    assert is_significant is significant
    assert (note is None) is significant
    assert 0.0 <= p_value <= 1.0
    if current + previous < MIN_TREND_SAMPLE:
        assert "amostra insuficiente" in note


def test_trend_nao_afirma_direcao_sem_amostra(db):
    cover(db)
    publish(db, "a", 1)
    publish(db, "b", 100)

    trend = metrics(db).trend(ALL, days=90)

    assert trend.current.complaints == 1
    assert trend.previous.complaints == 1
    assert trend.significant is False
    assert trend.direction is None
    assert "amostra insuficiente" in trend.note
    assert "coleta não cobriu" not in trend.note


def test_trend_afirma_alta_quando_a_diferenca_passa_o_teste(db):
    cover(db)
    for i in range(20):
        publish(db, f"atual{i}", i + 1)
    publish(db, "anterior", 100)

    trend = metrics(db).trend(ALL, days=90)

    assert (trend.current.complaints, trend.previous.complaints) == (20, 1)
    assert trend.significant is True
    assert trend.comparable is True
    assert trend.direction == "alta"
    assert trend.note is None
    assert trend.delta == 19
    assert trend.delta_pct == 1900.0


def test_trend_sem_periodo_anterior_nao_calcula_variacao_relativa(db):
    cover(db)
    for i in range(12):
        publish(db, f"atual{i}", i + 1)

    trend = metrics(db).trend(ALL, days=90)

    assert trend.previous.complaints == 0
    assert trend.delta_pct is None
    assert trend.direction == "alta"


def test_trend_sem_coleta_na_janela_anterior_nao_afirma_direcao(db):
    """O caso do banco real: uma coleta só, e todo o volume no período mais recente."""
    searched(db, "atual", 0)
    for i in range(20):
        publish(db, f"atual{i}", i + 1)
    publish(db, "anterior", 100)

    trend = metrics(db).trend(ALL, days=90)

    # A diferença é grande e o teste estatístico passa…
    assert trend.significant is True
    # …mas a janela anterior não teve coleta própria, então não há tendência a afirmar.
    assert trend.comparable is False
    assert trend.direction is None
    assert "coleta não cobriu as duas janelas" in trend.note
    assert (trend.current.searches, trend.previous.searches) == (1, 0)


def test_trend_nomeia_as_duas_condicoes_quando_as_duas_falham(db):
    """Sem coleta na janela anterior e com amostra pequena: a nota diz as duas coisas."""
    searched(db, "atual", 0)
    publish(db, "a", 1)
    publish(db, "b", 100)

    trend = metrics(db).trend(ALL, days=90)

    assert trend.comparable is False
    assert trend.significant is False
    assert "coleta não cobriu as duas janelas" in trend.note
    assert "amostra insuficiente" in trend.note


def test_trend_nao_conta_dias_posteriores_a_ultima_coleta(db):
    """Regressão do caso real: a janela ia até hoje, mas a coleta parou dias antes.

    Os dias sem coleta não têm reclamação por construção — ninguém foi buscar — e
    entravam na conta como se o volume tivesse caído.
    """
    searched(db, "unica", 4)
    for i in range(12):
        publish(db, f"coletado{i}", i + 5)

    trend = metrics(db).trend(ALL, days=30)

    # A janela termina na última coleta, não hoje.
    assert trend.current.end == utc_now().date() - timedelta(days=4)
    assert trend.current.complaints == 12


def test_trend_janelas_tem_a_mesma_duracao_e_nao_se_sobrepoem(db):
    trend = metrics(db).trend(ALL, days=30)

    assert (trend.current.end - trend.current.start).days == 29
    assert (trend.previous.end - trend.previous.start).days == 29
    assert trend.previous.end < trend.current.start


# ---------------------------------------------------------------------- API


def test_api_expoe_metricas_do_escopo(client):
    db = client.app.state.db
    make_product(db)
    seed_confirmed(db)
    make_possible(db, "poss")
    run_matching(db, MatchRequest())

    overview = client.get("/dashboard/overview").json()
    assert overview["matches"]["confirmed"] == 1
    assert overview["matches"]["pending"] == 1
    assert overview["filters"]["category"] is None

    top = client.get("/dashboard/top-products", params={"limit": 5}).json()
    assert top[0]["name"] == "Ultraformer III"

    timeline = client.get("/dashboard/timeline", params={"granularity": "week"}).json()
    assert timeline["granularity"] == "week"

    trend = client.get("/dashboard/trend", params={"days": 30}).json()
    assert trend["days"] == 30
    assert trend["direction"] is None
    assert trend["significant"] is False


def test_api_ecoa_o_escopo_aplicado(client):
    response = client.get(
        "/dashboard/overview",
        params={
            "category": "equipamentos",
            "subcategory": "ESTETICA",
            "date_from": "2025-01-01",
            "date_to": "2025-12-31",
        },
    )

    assert response.status_code == 200
    # O escopo volta como o backend o entendeu: subcategoria em forma canônica.
    assert response.json()["filters"] == {
        "product_id": None,
        "category": "equipamentos",
        "subcategory": "estetica",
        "date_from": "2025-01-01",
        "date_to": "2025-12-31",
    }


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/dashboard/overview", {"category": "inexistente"}),
        ("/dashboard/timeline", {"granularity": "trimestre"}),
        ("/dashboard/trend", {"days": 3}),
        ("/dashboard/overview", {"date_from": "10/01/2025"}),
        # Recorte invertido: erro de quem pergunta, não dashboard vazio.
        ("/dashboard/overview", {"date_from": "2025-12-31", "date_to": "2025-01-01"}),
        ("/dashboard/timeline", {"date_from": "2025-12-31", "date_to": "2025-01-01"}),
    ],
)
def test_api_rejeita_escopo_invalido(client, path, params):
    assert client.get(path, params=params).status_code == 422


def test_api_serve_a_pagina_do_dashboard(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "RA Product Monitor" in response.text
