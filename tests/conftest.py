"""Fixtures compartilhadas: banco temporário e cliente da API.

Cada teste recebe um DuckDB próprio em `tmp_path`; nada aqui toca rede nem o banco real.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.duckdb")
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
