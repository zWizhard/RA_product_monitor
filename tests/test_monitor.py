"""Testes do monitoramento periódico: coleta de todos os produtos, matching e relatório.

Nenhum teste abre navegador: o coletor é substituído por um falso que devolve registros
brutos por termo.
"""

import asyncio
import json

from app.collector import FetchResult
from app.monitor import _md, main, run_monitor
from tests.helpers import make_product
from tests.test_collect import raw_item


class TermCollector:
    """Devolve registros fixos por termo; termo em `failing` levanta erro."""

    source = "reclame_aqui"

    def __init__(
        self,
        items: dict[str, list[dict]],
        failing: set[str] = frozenset(),
        blocked: set[str] = frozenset(),
    ):
        self.items = items
        self.failing = failing
        self.blocked = blocked
        self.calls: list[str] = []

    async def fetch(self, term: str, pages: int) -> FetchResult:
        self.calls.append(term)
        if term in self.failing:
            raise RuntimeError("timeout de navegação")
        if term in self.blocked:
            # Como o coletor real devolve o desafio do Cloudflare: sem exceção.
            return FetchResult(notes=["página 1: navegação bloqueada (status 403)"], failures=1)
        return FetchResult(items=[dict(item) for item in self.items.get(term, [])])


ULTRA = raw_item(
    "ultraformer-queimou_a1",
    title="Ultraformer III da Classys causou queimadura",
    description="Fiquei com bolhas e queimadura no rosto após sessão com o Ultraformer III.",
    descriptionMasked=None,
)


def _run(db, tmp_path, collector, **kwargs):
    return asyncio.run(run_monitor(db, collector, report_dir=tmp_path / "reports", **kwargs))


def _monitor_row(db, run_id):
    with db.reading() as conn:
        return conn.execute(
            "SELECT status, products_searched, inserted, errors, details, report_path,"
            " finished_at FROM monitor_run WHERE id = ?",
            [run_id],
        ).fetchone()


def test_execucao_completa_coleta_casa_e_gera_relatorio(db, tmp_path):
    make_product(db)
    collector = TermCollector({"ultrassom microfocado": [ULTRA]})

    outcome = _run(db, tmp_path, collector)

    assert outcome.status == "completed"
    assert collector.calls == ["ultrassom microfocado"]
    assert (outcome.products_searched, outcome.inserted) == (1, 1)
    status, products, inserted, errors, details, path, finished = _monitor_row(
        db, outcome.run_id
    )
    assert (status, products, inserted, json.loads(errors)) == ("completed", 1, 1, [])
    assert json.loads(details)[0]["search_runs"][0]["inserted"] == 1
    assert finished is not None
    report = outcome.report_path.read_text(encoding="utf-8")
    assert path == str(outcome.report_path)
    for section in (
        "## Resumo executivo",
        "## Produtos",
        "## Problemas",
        "## Sinais",
        "## Qualidade do matching",
        "## Pendências",
        "## Controle de execução",
    ):
        assert section in report
    assert "| Reclamações novas coletadas | 1 |" in report
    assert "| … associadas a produto monitorado | 1 |" in report
    assert "| Possíveis eventos adversos | 1 |" in report


def test_segunda_execucao_nao_duplica_reclamacoes(db, tmp_path):
    make_product(db)
    collector = TermCollector({"ultrassom microfocado": [ULTRA]})

    _run(db, tmp_path, collector)
    again = _run(db, tmp_path, collector)

    assert (again.collected, again.inserted) == (1, 0)
    with db.reading() as conn:
        assert conn.execute("SELECT count(*) FROM complaint").fetchone()[0] == 1


def test_falha_de_um_produto_nao_cancela_os_demais(db, tmp_path):
    make_product(db, name="Aparelho que falha", search_terms=["falha"], brand="X", model="Y",
                 aliases=[])
    make_product(db)
    collector = TermCollector({"ultrassom microfocado": [ULTRA]}, failing={"falha"})

    outcome = _run(db, tmp_path, collector)

    assert outcome.status == "partial"
    assert outcome.inserted == 1
    assert len(outcome.errors) == 1 and "falha" in outcome.errors[0]
    status, products, *_ = _monitor_row(db, outcome.run_id)
    assert (status, products) == ("partial", 2)
    assert "timeout de navegação" in outcome.report_path.read_text(encoding="utf-8")


def test_bloqueio_da_fonte_torna_execucao_parcial(db, tmp_path):
    make_product(db)
    collector = TermCollector({}, blocked={"ultrassom microfocado"})

    outcome = _run(db, tmp_path, collector)

    assert outcome.status == "partial"
    assert len(outcome.errors) == 1 and "bloqueada" in outcome.errors[0]
    status, *_ = _monitor_row(db, outcome.run_id)
    assert status == "partial"


def test_execucao_interrompida_anterior_fica_como_falha(db, tmp_path):
    make_product(db)
    with db.transaction() as conn:
        orphan = conn.execute(
            "INSERT INTO monitor_run (source, status, period_start, period_end, started_at)"
            " VALUES ('reclame_aqui', 'running', now(), now(), now()) RETURNING id"
        ).fetchone()[0]

    outcome = _run(db, tmp_path, TermCollector({}), collect=False)

    status, *_, errors, _, _, finished = _monitor_row(db, orphan)
    assert status == "failed" and finished is not None
    assert json.loads(errors) == ["interrompida antes de terminar"]
    assert _monitor_row(db, outcome.run_id)[0] == "completed"


def test_sem_coleta_so_gera_relatorio(db, tmp_path):
    make_product(db)
    collector = TermCollector({})

    outcome = _run(db, tmp_path, collector, collect=False)

    assert outcome.status == "completed"
    assert collector.calls == []
    assert outcome.report_path.exists()


def test_texto_externo_nao_vira_marcacao():
    escaped = _md("<script>x</script> | [link](http://x) **n**\nquebra")
    assert "<" not in escaped and "\n" not in escaped
    assert "\\|" in escaped and "\\[" in escaped and "\\*" in escaped


def test_cli_banco_em_uso_informa_e_sai(tmp_path, monkeypatch, capsys):
    import duckdb

    from app.config import get_settings

    monkeypatch.setenv("RAPM_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    def locked(self):
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr("app.monitor.Database.connect", locked)
    try:
        assert main(["--sem-coleta"]) == 2
    finally:
        get_settings.cache_clear()
    assert "servidor está aberto" in capsys.readouterr().err
