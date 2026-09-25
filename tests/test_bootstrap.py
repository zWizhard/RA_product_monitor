"""Testes mínimos de bootstrap: configuração, banco, health check e ausência de legado."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, get_settings
from app.db import SCHEMA_VERSION, Database


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPM_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def client(isolated_settings):
    from app.main import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1") as c:
        yield c


def test_db_path_previsivel_a_partir_da_raiz(monkeypatch):
    monkeypatch.delenv("RAPM_DATA_DIR", raising=False)
    get_settings.cache_clear()
    try:
        db_path = get_settings().db_path
    finally:
        get_settings.cache_clear()
    assert db_path == PROJECT_ROOT / "data" / "ra_product_monitor.duckdb"
    assert db_path.is_absolute()


def test_data_dir_relativo_resolve_contra_a_raiz(monkeypatch):
    monkeypatch.setenv("RAPM_DATA_DIR", "runtime")
    get_settings.cache_clear()
    try:
        assert get_settings().data_dir == (PROJECT_ROOT / "runtime").resolve()
    finally:
        get_settings.cache_clear()


def test_banco_cria_arquivo_e_schema(tmp_path):
    db = Database(tmp_path / "sub" / "teste.duckdb")
    db.connect()
    try:
        assert db.path.exists()
        assert db.healthy()
        assert db.schema_version() == SCHEMA_VERSION
    finally:
        db.close()


def test_conexao_nao_iniciada_falha_explicitamente(tmp_path):
    with pytest.raises(RuntimeError):
        Database(tmp_path / "teste.duckdb").conn


def test_health_ok(client, isolated_settings):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["database"]["ok"] is True
    assert body["database"]["path"] == str(isolated_settings.db_path)
    assert isolated_settings.db_path.exists()


def test_app_nao_depende_da_base_antiga():
    """A nova base não pode referenciar o diretório ou o banco do RA Intelligence."""
    proibidos = ("ra_intelligence", "RA/backend", "RA\backend")
    for arquivo in Path(PROJECT_ROOT / "app").rglob("*.py"):
        conteudo = arquivo.read_text(encoding="utf-8")
        for termo in proibidos:
            assert termo not in conteudo, f"{arquivo} referencia legado: {termo}"


def test_host_fora_da_lista_e_recusado(client):
    """Sem autenticação, a API não atende por IP de rede nem por DNS rebinding."""
    assert client.get("/health", headers={"host": "192.168.0.10:8000"}).status_code == 400
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/health", headers={"host": "localhost:8000"}).status_code == 200


def test_escrita_de_outra_origem_e_recusada(client):
    payload = {"name": "X", "category": "equipamentos_estetica"}
    for origin in ("https://evil.example", "http://localhost:3000", "null"):
        r = client.post("/products", json=payload, headers={"origin": origin})
        assert r.status_code == 403, origin
    # Leitura de outra origem não é bloqueada aqui: sem CORS, o navegador não entrega a resposta.
    assert client.get("/products", headers={"origin": "https://evil.example"}).status_code == 200


def test_escrita_da_propria_origem_ou_sem_origin_passa(client):
    r = client.post("/products/999/deactivate", headers={"origin": "http://127.0.0.1"})
    assert r.status_code == 404
    assert client.post("/products/999/deactivate").status_code == 404
