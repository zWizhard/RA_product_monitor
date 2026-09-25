"""Testes do cadastro de produtos: normalização, taxonomia, persistência e API."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Database
from app.models import ProductCreate, ProductUpdate
from app.normalization import dedupe_terms, normalize_text
from app.repository import (
    DuplicateProduct,
    ProductNotFound,
    ProductRepository,
    natural_key,
)
from app.taxonomy import Category, InvalidTaxonomy, validate_pair

EQUIPAMENTO = {
    "name": "Ultraformer III",
    "category": "equipamentos",
    "subcategory": "estetica",
    "brand": "Classys",
    "model": "MPT",
    "manufacturer": "Classys Inc.",
    "regulatory_id": "80102510999",
    "aliases": ["HIFU", "Ultraformer"],
    "search_terms": ["ultraformer iii"],
}
GLICOSIMETRO = {
    "name": "Accu-Chek Guide",
    "category": "diagnostico_in_vitro",
    "subcategory": "glicosimetro",
    "brand": "Roche",
}


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "produtos.duckdb")
    database.connect()
    yield database
    database.close()


@pytest.fixture
def repo(db):
    return ProductRepository(db)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPM_DATA_DIR", str(tmp_path / "api"))
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1") as c:
        yield c
    get_settings.cache_clear()


# ------------------------------------------------------------- normalização


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Ácido Hialurônico", "acido hialuronico"),
        ("  Accu-Chek   Guide ", "accu chek guide"),
        ("PMMA 30%", "pmma 30"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_text(entrada, esperado):
    assert normalize_text(entrada) == esperado


def test_dedupe_terms_remove_vazios_e_repetidos_preservando_texto():
    assert dedupe_terms(["HIFU", " hifu ", "", "  ", "Ultraformer"]) == ["HIFU", "Ultraformer"]


def test_natural_key_ignora_acento_caixa_e_pontuacao():
    assert natural_key("Ultraformer III", "Classys", "MPT") == natural_key(
        " ultraformer-iii ", "CLASSYS", "mpt"
    )


# ---------------------------------------------------------------- taxonomia


def test_validate_pair_aceita_subcategoria_da_categoria():
    assert validate_pair(Category.MATERIAIS_IMPLANTAVEIS, "Mamario") == "mamario"


@pytest.mark.parametrize("subcategoria", [None, "", "pmma"])
def test_validate_pair_rejeita_subcategoria_invalida(subcategoria):
    with pytest.raises(InvalidTaxonomy):
        validate_pair(Category.MATERIAIS_IMPLANTAVEIS, subcategoria)


# -------------------------------------------------------------- persistência


def test_create_persiste_campos_aliases_e_termos(repo):
    produto = repo.create(ProductCreate(**EQUIPAMENTO))

    assert produto.id > 0
    assert produto.name == "Ultraformer III"
    assert produto.category is Category.EQUIPAMENTOS
    assert produto.subcategory == "estetica"
    assert produto.manufacturer == "Classys Inc."
    assert produto.regulatory_id == "80102510999"
    assert produto.aliases == ["HIFU", "Ultraformer"]
    assert produto.search_terms == ["ultraformer iii"]
    assert produto.active is True
    assert produto.natural_key == "ultraformer iii|classys|mpt"
    assert repo.get(produto.id) == produto


def test_create_rejeita_duplicidade_por_chave_natural(repo):
    original = repo.create(ProductCreate(**EQUIPAMENTO))
    variacao = dict(EQUIPAMENTO, name="ultraformer  iii", brand="CLASSYS", model="mpt")

    with pytest.raises(DuplicateProduct) as erro:
        repo.create(ProductCreate(**variacao))

    assert erro.value.existing_id == original.id
    assert len(repo.list()) == 1


def test_produtos_com_marca_ou_modelo_diferentes_nao_sao_duplicados(repo):
    repo.create(ProductCreate(**EQUIPAMENTO))
    outro = repo.create(ProductCreate(**dict(EQUIPAMENTO, model="Classic")))
    assert outro.natural_key != "ultraformer iii|classys|mpt"


def test_list_filtra_por_categoria_situacao_e_busca(repo):
    equipamento = repo.create(ProductCreate(**EQUIPAMENTO))
    glicosimetro = repo.create(ProductCreate(**GLICOSIMETRO))

    assert [p.id for p in repo.list()] == [glicosimetro.id, equipamento.id]  # ordem por nome
    assert [p.id for p in repo.list(category=Category.DIAGNOSTICO_IN_VITRO)] == [glicosimetro.id]
    assert [p.id for p in repo.list(subcategory="estetica")] == [equipamento.id]
    assert [p.id for p in repo.list(subcategory=" Estetica ")] == [equipamento.id]  # caixa livre
    assert [p.id for p in repo.list(query="hifu")] == [equipamento.id]  # casa por alias
    assert [p.id for p in repo.list(query="ACCU chek")] == [glicosimetro.id]  # casa por nome
    assert repo.list(query="marca inexistente") == []

    repo.set_active(equipamento.id, False)
    assert [p.id for p in repo.list(active=True)] == [glicosimetro.id]
    assert [p.id for p in repo.list(active=False)] == [equipamento.id]


def test_update_parcial_altera_apenas_o_enviado(repo):
    produto = repo.create(ProductCreate(**EQUIPAMENTO))

    atualizado = repo.update(produto.id, ProductUpdate(manufacturer="Classys Brasil"))

    assert atualizado.manufacturer == "Classys Brasil"
    assert atualizado.name == produto.name
    assert atualizado.aliases == produto.aliases
    assert atualizado.search_terms == produto.search_terms
    assert atualizado.created_at == produto.created_at
    assert atualizado.updated_at >= produto.updated_at


def test_update_recalcula_chave_natural_e_substitui_termos(repo):
    produto = repo.create(ProductCreate(**EQUIPAMENTO))

    atualizado = repo.update(produto.id, ProductUpdate(name="Ultraformer MPT", aliases=["HIFU MPT"]))

    assert atualizado.natural_key == "ultraformer mpt|classys|mpt"
    assert atualizado.aliases == ["HIFU MPT"]
    assert atualizado.search_terms == produto.search_terms


def test_update_rejeita_colisao_com_outro_produto_sem_alterar_nada(repo):
    primeiro = repo.create(ProductCreate(**EQUIPAMENTO))
    segundo = repo.create(ProductCreate(**GLICOSIMETRO))

    with pytest.raises(DuplicateProduct):
        repo.update(
            segundo.id,
            ProductUpdate(name=primeiro.name, brand=primeiro.brand, model=primeiro.model),
        )

    assert repo.get(segundo.id) == segundo


def test_update_valida_par_categoria_subcategoria(repo):
    produto = repo.create(ProductCreate(**GLICOSIMETRO))

    with pytest.raises(InvalidTaxonomy):
        repo.update(produto.id, ProductUpdate(subcategory="pmma"))

    migrado = repo.update(
        produto.id, ProductUpdate(category=Category.MATERIAIS_ESTETICOS, subcategory="pmma")
    )
    assert (migrado.category, migrado.subcategory) == (Category.MATERIAIS_ESTETICOS, "pmma")


def test_desativar_preserva_id_datas_e_termos(repo):
    produto = repo.create(ProductCreate(**EQUIPAMENTO))

    desativado = repo.set_active(produto.id, False)

    assert desativado.active is False
    assert desativado.id == produto.id
    assert desativado.created_at == produto.created_at
    assert desativado.aliases == produto.aliases
    assert repo.set_active(produto.id, True).active is True


def test_get_de_produto_inexistente_falha(repo):
    with pytest.raises(ProductNotFound):
        repo.get(9999)


def test_dados_sobrevivem_a_reconexao(tmp_path):
    caminho = tmp_path / "persistente.duckdb"
    primeira = Database(caminho)
    primeira.connect()
    produto = ProductRepository(primeira).create(ProductCreate(**EQUIPAMENTO))
    primeira.close()

    segunda = Database(caminho)
    segunda.connect()
    try:
        recuperado = ProductRepository(segunda).get(produto.id)
    finally:
        segunda.close()

    assert recuperado == produto


# ---------------------------------------------------------------------- API


def test_api_cadastra_consulta_edita_e_desativa(client):
    criado = client.post("/products", json=EQUIPAMENTO)
    assert criado.status_code == 201
    produto = criado.json()
    assert produto["aliases"] == ["HIFU", "Ultraformer"]

    assert client.get(f"/products/{produto['id']}").json() == produto

    editado = client.patch(f"/products/{produto['id']}", json={"brand": "Classys Korea"})
    assert editado.status_code == 200
    assert editado.json()["brand"] == "Classys Korea"
    assert editado.json()["natural_key"] == "ultraformer iii|classys korea|mpt"

    desativado = client.post(f"/products/{produto['id']}/deactivate")
    assert desativado.status_code == 200
    assert desativado.json()["active"] is False
    assert client.get("/products", params={"active": True}).json() == []
    assert client.post(f"/products/{produto['id']}/activate").json()["active"] is True


def test_api_duplicidade_retorna_409(client):
    assert client.post("/products", json=EQUIPAMENTO).status_code == 201
    repetido = client.post("/products", json=dict(EQUIPAMENTO, name="ULTRAFORMER III"))
    assert repetido.status_code == 409
    assert "duplicado" in repetido.json()["detail"]


def test_api_produto_inexistente_retorna_404(client):
    assert client.get("/products/4242").status_code == 404
    assert client.patch("/products/4242", json={"brand": "X"}).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        dict(EQUIPAMENTO, category="categoria_inexistente"),
        dict(EQUIPAMENTO, subcategory="mamario"),
        dict(EQUIPAMENTO, subcategory=None),
        dict(EQUIPAMENTO, name="A"),
    ],
)
def test_api_payload_invalido_retorna_422(client, payload):
    assert client.post("/products", json=payload).status_code == 422


def test_api_patch_com_subcategoria_incompativel_retorna_422(client):
    produto = client.post("/products", json=GLICOSIMETRO).json()
    resposta = client.patch(f"/products/{produto['id']}", json={"subcategory": "pmma"})
    assert resposta.status_code == 422


def test_api_nao_expoe_remocao(client):
    produto = client.post("/products", json=EQUIPAMENTO).json()
    assert client.delete(f"/products/{produto['id']}").status_code == 405


def test_erro_inesperado_nao_e_mascarado_como_422(tmp_path, monkeypatch):
    """Só `InvalidTaxonomy` vira 422; falha interna continua 500."""
    monkeypatch.setenv("RAPM_DATA_DIR", str(tmp_path / "falha"))
    get_settings.cache_clear()
    from app.main import create_app
    from app.routers.products import get_repository

    class RepositorioQuebrado:
        def get(self, product_id: int):
            raise ValueError("falha interna inesperada")

    app = create_app()
    app.dependency_overrides[get_repository] = RepositorioQuebrado
    try:
        with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
            assert c.get("/products/1").status_code == 500
    finally:
        get_settings.cache_clear()


def test_api_busca_por_termo_e_categoria(client):
    client.post("/products", json=EQUIPAMENTO)
    client.post("/products", json=GLICOSIMETRO)

    assert [p["name"] for p in client.get("/products", params={"q": "hifu"}).json()] == [
        "Ultraformer III"
    ]
    encontrados = client.get("/products", params={"category": "diagnostico_in_vitro"}).json()
    assert [p["name"] for p in encontrados] == ["Accu-Chek Guide"]
    por_subcategoria = client.get("/products", params={"subcategory": "ESTETICA"}).json()
    assert [p["name"] for p in por_subcategoria] == ["Ultraformer III"]
