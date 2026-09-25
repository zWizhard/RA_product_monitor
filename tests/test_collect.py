"""Testes de busca e coleta: parsing, persistência, métricas e API.

Nenhum teste acessa a internet nem abre navegador: a navegação é substituída por um
coletor falso que devolve exatamente os registros que o endpoint de busca devolveria.
O arquivo `fixtures/busca_glicosimetro.json` é resposta real capturada em 17/09/2026
(dois registros e o `count` original).
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from app.collect_repository import ComplaintRepository, SearchRunRepository
from app.collect_service import resolve_terms, run_search, run_searches
from app.collector import (
    MAX_TEXT,
    PREVIEW_LENGTH,
    FetchResult,
    ItemDiscarded,
    ReclameAquiCollector,
    api_offset,
    build_complaint,
    canonical_url,
    clean_html,
    complaint_text,
    complaint_title,
    complaint_url,
    dedupe_key,
    is_search_api,
    parse_items,
    parse_published,
    records_from_payload,
    search_url,
)
from app.config import get_settings
from app.db import Database
from app.models import ProductCreate, SearchRequest
from app.repository import ProductRepository

RA = "https://www.reclameaqui.com.br"
FIXTURE = Path(__file__).parent / "fixtures" / "busca_glicosimetro.json"


def raw_item(slug: str, **overrides) -> dict:
    """Registro bruto como o endpoint de busca do Reclame Aqui devolve."""
    item = {
        "id": slug.rsplit("_", 1)[-1],
        "companyShortname": "clinica-x",
        "companyName": "Clínica X",
        "url": slug,
        "title": "Queimadura após sessão de HIFU",
        "description": "Fiquei com bolhas no rosto depois do procedimento.",
        "descriptionMasked": (
            "Fiquei com bolhas no rosto depois do procedimento.<br/>Atendi ****."
        ),
        "maskingStatus": "MASKED",
        "status": "ANSWERED",
        "solved": False,
        "userCity": "São Paulo",
        "userState": "SP",
        "created": "2024-03-12T14:22:00",
        "position": 0,
        "page": 1,
    }
    item.update(overrides)
    return item


def payload_records() -> list[dict]:
    """Os dois registros reais da resposta capturada."""
    records, _ = records_from_payload(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return [dict(record) for record in records]


class FakeCollector:
    """Substitui o Playwright: devolve registros brutos fixos ou levanta erro."""

    source = "reclame_aqui"

    def __init__(self, result: FetchResult | None = None, error: Exception | None = None):
        self.result = result or FetchResult()
        self.error = error
        self.calls: list[tuple[str, int]] = []

    async def fetch(self, term: str, pages: int) -> FetchResult:
        self.calls.append((term, pages))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "coleta.duckdb")
    database.connect()
    yield database
    database.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPM_DATA_DIR", str(tmp_path / "api"))
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1") as c:
        yield c
    get_settings.cache_clear()


# ------------------------------------------------------------------ URL e parsing


def test_search_url_escapa_termo():
    assert search_url("acido hialuronico", 2) == (
        f"{RA}/busca/?q=acido+hialuronico&pagina=2"
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://iosearch.reclameaqui.com.br/raichu-io-site-search-v1/query/x/10/20", True),
        (f"{RA}/busca/?q=x&pagina=3", False),
        ("https://iosearch.reclameaqui.com.br/outro-servico/query/x/10/0", False),
    ],
)
def test_is_search_api(url, expected):
    assert is_search_api(url) is expected


def test_api_offset_vem_do_proprio_endereco():
    base = "https://iosearch.reclameaqui.com.br/raichu-io-site-search-v1/query/glicosimetro"
    assert api_offset(f"{base}/10/20") == 20
    assert api_offset(f"{RA}/busca/?q=x") is None


@pytest.mark.parametrize(
    "href, expected",
    [
        ("/clinica-x/queimadura_ABC123/", f"{RA}/clinica-x/queimadura_ABC123"),
        (f"{RA}/clinica-x/queimadura_ABC123", f"{RA}/clinica-x/queimadura_ABC123"),
        ("http://reclameaqui.com.br/a/b_XY99/", "https://reclameaqui.com.br/a/b_XY99"),
        ("//www.reclameaqui.com.br/a/b_XY99", f"{RA}/a/b_XY99"),
        (f"{RA}/a/b_XY99/?utm_source=x#topo", f"{RA}/a/b_XY99"),
    ],
)
def test_canonical_url_normaliza(href, expected):
    assert canonical_url(href) == expected


@pytest.mark.parametrize(
    "href",
    ["", None, "   ", "https://exemplo.com/a/b_XY99", "javascript:void(0)", f"{RA}/", "/"],
)
def test_canonical_url_rejeita_link_inutilizavel(href):
    assert canonical_url(href) is None


def test_complaint_url_monta_link_publico_a_partir_da_fonte():
    """A URL pública é `companyShortname` + `url`; nada é deduzido."""
    assert complaint_url("abbott-freestyle", "libre-impreciso_ABC123") == (
        f"{RA}/abbott-freestyle/libre-impreciso_ABC123"
    )
    assert complaint_url("abbott-freestyle", "/libre-impreciso_ABC123/") == (
        f"{RA}/abbott-freestyle/libre-impreciso_ABC123"
    )


@pytest.mark.parametrize(
    "company, slug",
    [
        (None, "libre_ABC123"),
        ("", "libre_ABC123"),
        ("abbott-freestyle", None),
        ("abbott-freestyle", "   "),
        ("abbott-freestyle", "https://exemplo.com/reclamacao/1"),
        ("abbott-freestyle", "libre ABC123"),
        ("outra/empresa", "libre_ABC123"),
    ],
)
def test_complaint_url_recusa_registro_sem_link_real(company, slug):
    assert complaint_url(company, slug) is None


def test_dedupe_key_usa_id_da_fonte_ou_url():
    url = f"{RA}/a/b_XY99"
    assert dedupe_key("reclame_aqui", url, "XY99") == "reclame_aqui|XY99"
    assert dedupe_key("reclame_aqui", url, None) == f"reclame_aqui|{url}"


def test_clean_html_remove_marcacao_embutida():
    assert clean_html("Linha 1<br/>Linha 2 &amp; 3") == "Linha 1 Linha 2 & 3"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-09-12T14:43:09", datetime(2026, 9, 12, 14, 43, 9)),
        ("12/03/2024 às 14:22", datetime(2024, 3, 12, 14, 22)),
        ("12/03/2024", datetime(2024, 3, 12)),
        ("há 3 dias", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_published(text, expected):
    assert parse_published(text) == expected


# -------------------------------------------------------- resposta real capturada


def test_records_from_payload_le_a_resposta_real():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records, count = records_from_payload(payload)
    assert count == 561  # total declarado pela fonte para "glicosimetro"
    assert [record["companyShortname"] for record in records] == [
        "abbott-freestyle",
        "accumed-glicomed",
    ]


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"complainResult": None}, {"complainResult": {"complains": {"data": None}}}],
)
def test_records_from_payload_nao_adivinha_formato(payload):
    assert records_from_payload(payload) == ([], None)


def test_build_complaint_a_partir_de_registro_real():
    record = payload_records()[0]
    complaint = build_complaint(record)

    assert complaint.url == (
        f"{RA}/abbott-freestyle/aparelho-libre-2-plus-apresenta-erros-frequentes-"
        "perda-de-sinal-e-medicoes-imprecisas-gerando-inseguranca-e-frustracao_"
        "z-Q-deNd_YJjD-T5"
    )
    assert complaint.external_id == "z-Q-deNd_YJjD-T5"
    assert complaint.company == "Abbott FreeStyle"
    assert complaint.location == "Rio de Janeiro - RJ"
    assert complaint.status == "ANSWERED"
    assert complaint.published_at == datetime(2026, 9, 12, 14, 43, 9)
    assert complaint.published_text == "2026-09-12T14:43:09"
    assert "<br" not in complaint.description
    assert complaint.raw["descriptionMasked"] == record["descriptionMasked"]


def test_descricao_usa_o_texto_completo_e_nao_a_previa():
    """`description` vem truncada em ~130 caracteres; o texto completo é o mascarado."""
    record = payload_records()[0]
    complaint = build_complaint(record)
    assert len(record["description"]) == PREVIEW_LENGTH
    assert len(complaint.description) > len(record["description"])
    assert complaint.description.startswith(clean_html(record["description"])[:60])


def test_conteudo_mascarado_vence_o_original_no_titulo_e_no_texto():
    """Registro mascarado: grava-se o que a fonte mascarou, não o dado pessoal."""
    record = raw_item(
        "a_AAA111",
        maskingStatus="MASKED",
        title="Reclamação de Fulano de Tal",
        titleMasked="Reclamação de ****",
        description="Meu telefone é 11 90000-0000",
        descriptionMasked="Meu telefone é ****",
    )
    assert complaint_title(record) == "Reclamação de ****"
    assert complaint_text(record)[0] == "Meu telefone é ****"

    # Sem marcação de mascaramento, vale o que a fonte entregou como conteúdo.
    aberto = raw_item("b_BBB222", maskingStatus=None, title="Título aberto")
    assert complaint_title(aberto) == "Título aberto"


def test_texto_disponivel_so_em_previa_vira_nota():
    """Sem versão completa, o que existe é a prévia cortada — e isso não fica em silêncio."""
    record = raw_item(
        "a_AAA111",
        maskingStatus=None,
        description="x" * PREVIEW_LENGTH,
        descriptionMasked=None,
    )
    parsed = parse_items([record])
    assert len(parsed.items) == 1  # o registro é gravado assim mesmo
    assert parsed.discarded == []  # prévia curta não é falha
    assert len(parsed.notes) == 1 and "prévia" in parsed.notes[0]


def test_texto_acima_do_limite_e_cortado_com_nota_e_original_em_raw():
    inteiro = "a " * MAX_TEXT
    record = raw_item("a_AAA111", descriptionMasked=inteiro)
    parsed = parse_items([record])

    complaint = parsed.items[0].complaint
    assert MAX_TEXT - 1 <= len(complaint.description) <= MAX_TEXT
    assert complaint.raw["descriptionMasked"] == inteiro  # original intacto
    assert parsed.discarded == []
    assert len(parsed.notes) == 1 and "cortado" in parsed.notes[0]


# ----------------------------------------------------------------- descarte e lote


def test_build_complaint_preserva_fonte_e_conteudo():
    complaint = build_complaint(raw_item("queimadura_ABC123"))
    assert complaint.url == f"{RA}/clinica-x/queimadura_ABC123"
    assert complaint.external_id == "ABC123"
    assert complaint.title == "Queimadura após sessão de HIFU"
    assert complaint.status == "ANSWERED"
    assert complaint.location == "São Paulo - SP"
    assert complaint.published_at == datetime(2024, 3, 12, 14, 22)
    assert complaint.raw["url"] == "queimadura_ABC123"


def test_build_complaint_nunca_inventa_url():
    """O scraper antigo fabricava link; aqui o registro é descartado."""
    with pytest.raises(ItemDiscarded, match="sem link real"):
        build_complaint(raw_item("x", url=""))
    with pytest.raises(ItemDiscarded, match="sem link real"):
        build_complaint(raw_item("x", companyShortname=None))


def test_build_complaint_exige_titulo():
    with pytest.raises(ItemDiscarded):
        build_complaint(raw_item("queimadura_ABC123", title="  ", titleMasked=""))


def test_parse_items_separa_validos_de_descartes():
    parsed = parse_items(
        [
            raw_item("a_AAA111", position=0),
            raw_item("b_BBB222", url="", position=1),
            raw_item("c_CCC333", title="", titleMasked="", position=2),
            raw_item("d_DDD444", position=3, page=2),
        ]
    )
    assert [item.complaint.external_id for item in parsed.items] == ["AAA111", "DDD444"]
    assert [(item.page, item.position) for item in parsed.items] == [(1, 0), (2, 3)]
    assert len(parsed.discarded) == 2
    assert parsed.notes == []


# ------------------------------------------------------------------- persistência


def test_upsert_insere_uma_vez_e_preserva_conteudo_original(db):
    repo = ComplaintRepository(db)
    primeira = datetime(2026, 1, 10, 8, 0)
    segunda = datetime(2026, 2, 20, 9, 30)

    first = build_complaint(raw_item("queimadura_ABC123"))
    complaint_id, inserted = repo.upsert(first, primeira)
    assert inserted is True

    # Mesma reclamação, texto diferente e slug trocado: mesmo id da fonte.
    again = build_complaint(
        raw_item("outro-slug_ABC123", title="Título reescrito pelo site")
    )
    same_id, inserted_again = repo.upsert(again, segunda)
    assert (same_id, inserted_again) == (complaint_id, False)

    stored = repo.get(complaint_id)
    assert stored.title == "Queimadura após sessão de HIFU"  # evidência original intacta
    assert stored.collected_at == primeira
    assert stored.last_seen_at == segunda


def test_complaint_not_found(db):
    from app.collect_repository import ComplaintNotFound

    with pytest.raises(ComplaintNotFound):
        ComplaintRepository(db).get(999)


def test_search_run_not_found(db):
    from app.collect_repository import SearchRunNotFound

    with pytest.raises(SearchRunNotFound):
        SearchRunRepository(db).get(999)


# ------------------------------------------------------------------------ métricas


def test_execucao_produz_metricas_corretas(db):
    collector = FakeCollector(
        FetchResult(
            items=[
                raw_item("a_AAA111", position=0),
                raw_item("b_BBB222", position=1),
                raw_item("a_AAA111", position=2),  # repetida na mesma busca
                raw_item("c_CCC333", url="", position=3),  # sem link real
            ],
            notes=["página 2: resposta da busca sem reclamações — verificar formato"],
            failures=1,
        )
    )
    run = asyncio.run(
        run_search(db, term="hifu", pages=2, product_id=None, collector=collector)
    )

    assert collector.calls == [("hifu", 2)]
    assert run.status == "completed"
    assert run.found == 4
    assert run.collected == 3
    assert run.inserted == 2
    assert run.duplicates == 1
    assert run.failures == 2  # 1 descarte + 1 falha de página
    assert run.collected == run.inserted + run.duplicates
    assert any("verificar formato" in note for note in run.notes)

    # Reclamação repetida gera um único vínculo com a execução.
    complaints = ComplaintRepository(db).list(search_run_id=run.id)
    assert len(complaints) == 2


def test_segunda_execucao_so_conta_duplicados(db):
    collector = FakeCollector(FetchResult(items=[raw_item("a_AAA111")]))
    first = asyncio.run(
        run_search(db, term="hifu", pages=1, product_id=None, collector=collector)
    )
    second = asyncio.run(
        run_search(db, term="hifu", pages=1, product_id=None, collector=collector)
    )

    assert (first.inserted, first.duplicates) == (1, 0)
    assert (second.inserted, second.duplicates) == (0, 1)
    assert len(ComplaintRepository(db).list()) == 1


def test_falha_de_coleta_fica_registrada(db):
    collector = FakeCollector(error=TimeoutError("navegação expirou"))
    run = asyncio.run(
        run_search(db, term="pmma", pages=1, product_id=None, collector=collector)
    )

    assert run.status == "failed"
    assert run.finished_at is not None
    assert any("TimeoutError" in note for note in run.notes)
    assert SearchRunRepository(db).get(run.id).status == "failed"


def test_procedencia_registra_busca_e_termo(db):
    collector = FakeCollector(FetchResult(items=[raw_item("a_AAA111", position=14, page=2)]))
    run = asyncio.run(
        run_search(db, term="ultraformer", pages=2, product_id=None, collector=collector)
    )

    repo = ComplaintRepository(db)
    complaint = repo.list(search_run_id=run.id)[0]
    detail = repo.detail(complaint.id)
    assert len(detail.found_by) == 1
    hit = detail.found_by[0]
    assert (hit.search_run_id, hit.term, hit.page, hit.position) == (run.id, "ultraformer", 2, 14)
    assert hit.first_seen is True


# --------------------------------------------------------------- navegação (local)

API = "https://iosearch.reclameaqui.com.br/raichu-io-site-search-v1/query/glicosimetro"


def api_url(offset: int, size: int = 10) -> str:
    return f"{API}/{size}/{offset}"


class FakeResponse:
    """Resposta do Playwright reduzida ao que o coletor usa."""

    def __init__(self, url: str, *, status: int = 200, payload=None):
        self.url = url
        self.status = status
        self._payload = payload

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    async def json(self):
        return self._payload


class FakePage:
    """Página do Playwright reduzida ao que `_collect_page` usa: navegar e capturar."""

    def __init__(
        self,
        response: FakeResponse | None = None,
        goto_error: Exception | None = None,
        navigation: FakeResponse | None = None,
    ):
        self.response = response
        self.goto_error = goto_error
        self.navigation = navigation
        self.visited: list[str] = []

    def expect_response(self, predicate, timeout=None):
        page = self

        class _Expect:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            @property
            def value(self):
                async def _resolve():
                    if page.response is None:
                        raise TimeoutError("nenhuma resposta da busca")
                    assert predicate(page.response)
                    return page.response

                return _resolve()

        return _Expect()

    async def goto(self, url: str, **kwargs):
        self.visited.append(url)
        if self.goto_error is not None:
            raise self.goto_error
        return self.navigation


def collect_one_page(page: FakePage, *, page_num: int = 1, offset: int = 0):
    """Roda a captura de uma página de resultados sem rede e sem navegador."""
    collector = ReclameAquiCollector(interval_ms=0)
    result = FetchResult()
    count, blocked = asyncio.run(
        collector._collect_page(page, "glicosimetro", page_num, offset, result)
    )
    return count, blocked, result


def test_captura_da_busca_registra_itens_com_posicao_e_procedencia():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page = FakePage(FakeResponse(api_url(10), payload=payload))
    count, blocked, result = collect_one_page(page, page_num=2, offset=10)

    assert (count, blocked) == (561, False)
    assert (result.failures, result.notes) == (0, [])
    assert page.visited == [search_url("glicosimetro", 2)]
    assert [item["position"] for item in result.items] == [10, 11]
    assert {item["page"] for item in result.items} == {2}
    assert {item["search_url"] for item in result.items} == {search_url("glicosimetro", 2)}


def test_busca_bloqueada_conta_falha_com_o_status_visivel():
    """403 no endpoint não pode virar 'página sem resultados'."""
    _, blocked, result = collect_one_page(FakePage(FakeResponse(api_url(0), status=403)))

    assert (result.failures, result.items, blocked) == (1, [], False)
    assert "403" in result.notes[0]


def test_navegacao_barrada_interrompe_a_paginacao():
    """Desafio do Cloudflare no documento: anotar e parar, em vez de insistir."""
    page = FakePage(navigation=FakeResponse(search_url("glicosimetro", 2), status=403))
    count, blocked, result = collect_one_page(page, page_num=2, offset=10)

    assert (count, blocked, result.failures, result.items) == (None, True, 1, [])
    assert "paginação interrompida" in result.notes[0]
    assert "403" in result.notes[0]


def test_formato_inesperado_da_resposta_conta_falha():
    page = FakePage(FakeResponse(api_url(0), payload={"complainResult": {}}))
    _, _, result = collect_one_page(page)

    assert (result.failures, result.items) == (1, [])
    assert "verificar formato" in result.notes[0]


def test_falha_de_navegacao_e_anotada_sem_interromper():
    page = FakePage(goto_error=TimeoutError("navegação expirou"))
    count, blocked, result = collect_one_page(page)

    assert (count, blocked, result.failures, result.items) == (None, False, 1, [])
    assert "TimeoutError" in result.notes[0]


def test_offset_divergente_e_anotado_e_manda_na_posicao():
    """Se a página pedir outro offset, a posição segue o que a fonte de fato devolveu."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page = FakePage(FakeResponse(api_url(0), payload=payload))
    _, _, result = collect_one_page(page, page_num=2, offset=10)

    assert [item["position"] for item in result.items] == [0, 1]
    assert "offset capturado 0, esperado 10" in result.notes[0]


def test_intervalo_entre_requisicoes_vale_tambem_entre_execucoes():
    """O ritmo é por coletor, não por execução: dois termos seguidos não colam."""
    collector = ReclameAquiCollector(interval_ms=120)

    async def duas_requisicoes() -> float:
        await collector._pace()  # última requisição do termo anterior
        inicio = monotonic()
        await collector._pace()  # primeira requisição do termo seguinte
        return monotonic() - inicio

    assert asyncio.run(duas_requisicoes()) >= 0.1


@pytest.mark.skipif(sys.platform != "win32", reason="regressão específica do Windows")
def test_fetch_roda_em_loop_com_suporte_a_subprocesso(monkeypatch):
    """Regressão: com `uvicorn --reload` o loop é `Selector` e não abre subprocesso.

    O navegador é subprocesso, então a coleta precisa rodar em loop `Proactor` mesmo
    quando quem chama está em um loop que não suporta isso. Nada de rede aqui: só a
    escolha do loop é exercitada.
    """
    collector = ReclameAquiCollector()

    async def fake_fetch(term: str, pages: int) -> FetchResult:
        return FetchResult(notes=[type(asyncio.get_running_loop()).__name__])

    monkeypatch.setattr(collector, "_fetch", fake_fetch)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        result = runner.run(collector.fetch("glicosimetro", 1))

    assert result.notes == ["ProactorEventLoop"]


# ------------------------------------------------------------------ termos e produto


def test_resolve_terms_usa_termos_de_busca_do_produto(db):
    product = ProductRepository(db).create(
        ProductCreate(
            name="Ultraformer III",
            category="equipamentos",
            subcategory="estetica",
            search_terms=["ultraformer iii", "hifu classys"],
        )
    )
    request = SearchRequest(product_id=product.id, pages=1)
    assert resolve_terms(db, request) == ["ultraformer iii", "hifu classys"]


def test_resolve_terms_cai_no_nome_quando_nao_ha_termos(db):
    product = ProductRepository(db).create(
        ProductCreate(name="Accu-Chek Guide", category="diagnostico_in_vitro",
                      subcategory="glicosimetro")
    )
    assert resolve_terms(db, SearchRequest(product_id=product.id)) == ["Accu-Chek Guide"]


def test_search_request_exige_produto_ou_termos():
    with pytest.raises(ValueError):
        SearchRequest(pages=1)


def test_uma_execucao_por_termo(db):
    product = ProductRepository(db).create(
        ProductCreate(
            name="Preenchedor AH",
            category="materiais_esteticos",
            subcategory="acido_hialuronico",
            search_terms=["acido hialuronico", "preenchimento labial"],
        )
    )
    collector = FakeCollector(FetchResult(items=[raw_item("a_AAA111")]))
    runs = asyncio.run(
        run_searches(db, SearchRequest(product_id=product.id, pages=1), collector)
    )

    assert [run.term for run in runs] == ["acido hialuronico", "preenchimento labial"]
    assert all(run.product_id == product.id for run in runs)
    assert collector.calls == [("acido hialuronico", 1), ("preenchimento labial", 1)]
    # A mesma reclamação encontrada por dois termos continua sendo um único registro.
    assert len(ComplaintRepository(db).list(product_id=product.id)) == 1


# ------------------------------------------------------------------------------ API


def test_api_executa_busca_e_expoe_metricas(client):
    client.app.state.collector = FakeCollector(
        FetchResult(items=[raw_item("a_AAA111"), raw_item("b_BBB222", url="")])
    )
    response = client.post("/searches", json={"terms": ["pmma"], "pages": 1})
    assert response.status_code == 200
    run = response.json()[0]
    assert (run["found"], run["collected"], run["inserted"], run["failures"]) == (2, 1, 1, 1)

    listed = client.get("/searches", params={"status": "completed"}).json()
    assert [item["id"] for item in listed] == [run["id"]]
    assert client.get(f"/searches/{run['id']}").json()["term"] == "pmma"
    assert client.get("/searches/999").status_code == 404


def test_api_lista_e_detalha_reclamacao(client):
    client.app.state.collector = FakeCollector(FetchResult(items=[raw_item("a_AAA111")]))
    run = client.post("/searches", json={"terms": ["hifu"], "pages": 1}).json()[0]

    complaints = client.get("/complaints", params={"q": "queimadura"}).json()
    assert len(complaints) == 1
    assert complaints[0]["url"] == f"{RA}/clinica-x/a_AAA111"

    detail = client.get(f"/complaints/{complaints[0]['id']}").json()
    assert detail["found_by"][0]["term"] == "hifu"
    assert detail["found_by"][0]["search_run_id"] == run["id"]
    assert client.get("/complaints/999").status_code == 404


def test_api_rejeita_busca_sem_origem(client):
    assert client.post("/searches", json={"pages": 1}).status_code == 422
