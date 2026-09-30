"""
Monitoramento periódico: produtos ativos -> coleta -> matching -> relatório.

Uma execução faz, em ordem:
1. para cada produto ativo, uma busca por termo cadastrado (`collect_service`), com o
   mesmo coletor e o mesmo ritmo da coleta manual; reclamação já conhecida não é
   inserida de novo (`complaint.dedupe_key`);
2. matching das reclamações encontradas por essas buscas (`match_service`);
3. relatório Markdown do período, com números contados pelo banco (`analytics`) e a
   triagem por vocabulário de `relevance` — indício para leitura, não classificação.

Falha em um produto fica registrada em `monitor_run` e não interrompe os demais.

A rotina não depende de agendador: `python -m app.monitor` roda na hora, e qualquer
agendamento (Agendador de Tarefas, cron, container) só chama o mesmo comando.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from app.analytics import _LINKED, AnalyticsRepository, DashboardFilters
from app.collect_service import run_searches
from app.collector import Collector, ReclameAquiCollector
from app.config import get_settings
from app.db import Database
from app.match_repository import MatchRepository
from app.match_service import _MAX_PRODUCTS, run_matching
from app.matching import MatchStatus
from app.models import MatchRequest, SearchRequest, SearchRun
from app.relevance import ADVERSE, COMMERCIAL, NO_SIGNAL, RULES_VERSION, TECHNICAL, classify
from app.repository import ProductRepository, utc_now

# Critério de reincidência: reclamações novas do mesmo produto, no período, com indício
# de evento adverso. Abaixo disto é caso isolado, que a lista individual já mostra.
ADVERSE_REINCIDENCE_MIN = 2
# Linhas por tabela do relatório: o relatório resume, a interface detalha.
TOP_ROWS = 15

_NATURE_LABELS = {
    ADVERSE: "possível evento adverso",
    TECHNICAL: "possível queixa técnica",
    COMMERCIAL: "problema comercial",
    NO_SIGNAL: "sem sinal",
}


@dataclass
class MonitorOutcome:
    run_id: int
    status: str
    products_searched: int = 0
    search_runs: int = 0
    collected: int = 0
    inserted: int = 0
    errors: list[str] = field(default_factory=list)
    report_path: Path | None = None


# ------------------------------------------------------------------ execução


async def run_monitor(
    db: Database,
    collector: Collector,
    *,
    report_dir: Path,
    days: int = 7,
    pages: int = 1,
    collect: bool = True,
) -> MonitorOutcome:
    """Executa a rotina completa e registra o resultado, inclusive a falha."""
    started = utc_now()
    run_id = _start(db, collector.source, started, days)
    outcome = MonitorOutcome(run_id=run_id, status="running")
    details: list[dict] = []
    runs: list[SearchRun] = []
    matching: dict[str, object] = {}
    try:
        if collect:
            details, runs = await _collect_all(db, collector, pages, outcome.errors)
        matching = _match_collected(db, runs, outcome.errors)
        outcome.products_searched = len(details)
        outcome.search_runs = len(runs)
        outcome.collected = sum(run.collected for run in runs)
        outcome.inserted = sum(run.inserted for run in runs)
        outcome.status = "partial" if outcome.errors else "completed"
        # O período termina depois da coleta: o que acabou de entrar é da semana.
        period_end = utc_now()
        period_start = period_end - timedelta(days=days)
        report = build_report(
            db,
            period_start=period_start,
            period_end=period_end,
            control={
                "run_id": run_id,
                "status": outcome.status,
                "source": collector.source,
                "collect": collect,
                "started_at": started,
                "products": details,
                "search_runs": outcome.search_runs,
                "collected": outcome.collected,
                "inserted": outcome.inserted,
                "matching": matching,
                "errors": outcome.errors,
            },
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f"relatorio-semanal-{period_end:%Y-%m-%d}-exec-{run_id}.md"
        path.write_text(report, encoding="utf-8")
        outcome.report_path = path
    except Exception as exc:
        outcome.status = "failed"
        outcome.errors.append(f"execução interrompida ({type(exc).__name__}: {exc})")
        period_end = utc_now()
        period_start = period_end - timedelta(days=days)
    _finish(db, outcome, details, period_start, period_end)
    return outcome


async def _collect_all(
    db: Database, collector: Collector, pages: int, errors: list[str]
) -> tuple[list[dict], list[SearchRun]]:
    """Uma coleta por produto ativo; produto que falha não impede os seguintes."""
    details: list[dict] = []
    runs: list[SearchRun] = []
    for product in ProductRepository(db).list(active=True, limit=_MAX_PRODUCTS):
        label = f"produto {product.id} ({product.name})"
        try:
            product_runs = await run_searches(
                db, SearchRequest(product_id=product.id, pages=pages), collector
            )
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            details.append({"product_id": product.id, "name": product.name, "status": "failed"})
            continue
        runs.extend(product_runs)
        # Bloqueio da fonte não levanta erro: a busca termina `completed`, sem nada
        # coletado e com a falha contada. Para a rotina, isso também é falha.
        failed = [
            run
            for run in product_runs
            if run.status == "failed" or (run.failures > 0 and run.collected == 0)
        ]
        errors.extend(f"{label}, termo '{run.term}': {'; '.join(run.notes)}" for run in failed)
        details.append(
            {
                "product_id": product.id,
                "name": product.name,
                "status": "failed" if failed else "completed",
                "search_runs": [
                    {
                        "id": run.id,
                        "term": run.term,
                        "status": run.status,
                        "collected": run.collected,
                        "inserted": run.inserted,
                        "failures": run.failures,
                    }
                    for run in product_runs
                ],
            }
        )
    return details, runs


def _match_collected(
    db: Database, runs: list[SearchRun], errors: list[str]
) -> dict[str, object]:
    """Matching das reclamações que as buscas desta execução encontraram."""
    totals: Counter[str] = Counter()
    version = None
    for run in runs:
        if run.collected == 0:
            continue
        try:
            result = run_matching(db, MatchRequest(search_run_id=run.id, limit=5000))
        except Exception as exc:
            errors.append(f"matching da busca {run.id}: {type(exc).__name__}: {exc}")
            continue
        version = result.matcher_version
        for key in ("complaints_evaluated", "created", "updated", "stale", "confirmed", "possible"):
            totals[key] += getattr(result, key)
        if result.truncated:
            errors.append(f"matching da busca {run.id} parou no teto de produtos/reclamações")
    return {**totals, "matcher_version": version}


def _start(db: Database, source: str, started: datetime, days: int) -> int:
    with db.transaction() as conn:
        # O banco aceita um processo por vez e a rotina é sequencial: linha ainda
        # `running` aqui é de execução que morreu antes de terminar.
        conn.execute(
            "UPDATE monitor_run SET status = 'failed', finished_at = ?, errors = ?"
            " WHERE status = 'running'",
            [started, json.dumps(["interrompida antes de terminar"], ensure_ascii=False)],
        )
        return conn.execute(
            "INSERT INTO monitor_run (source, status, period_start, period_end, started_at)"
            " VALUES (?, 'running', ?, ?, ?) RETURNING id",
            [source, started - timedelta(days=days), started, started],
        ).fetchone()[0]


def _finish(
    db: Database,
    outcome: MonitorOutcome,
    details: list[dict],
    period_start: datetime,
    period_end: datetime,
) -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE monitor_run SET status = ?, period_start = ?, period_end = ?,
                products_searched = ?, search_runs = ?, collected = ?, inserted = ?,
                errors = ?, details = ?, report_path = ?, finished_at = ?
            WHERE id = ?
            """,
            [
                outcome.status,
                period_start,
                period_end,
                outcome.products_searched,
                outcome.search_runs,
                outcome.collected,
                outcome.inserted,
                json.dumps(outcome.errors, ensure_ascii=False),
                json.dumps(details, ensure_ascii=False),
                None if outcome.report_path is None else str(outcome.report_path),
                utc_now(),
                outcome.run_id,
            ],
        )


# ------------------------------------------------------------------ relatório


_MD_ESCAPE = str.maketrans(
    {"&": "&amp;", "<": "&lt;", ">": "&gt;"} | {c: f"\\{c}" for c in "\\`*_[]#|!"}
)


def _md(text: object, limit: int = 140) -> str:
    """Texto externo numa linha de Markdown, sem virar marcação nem HTML."""
    flat = " ".join(str(text or "").split())
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat.translate(_MD_ESCAPE)


def _link(url: str | None) -> str:
    """Link para a fonte original. URL que não é https não vira link."""
    if not url or not url.startswith("https://"):
        return "—"
    safe = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
    return f"[abrir]({safe})"


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return [*lines, ""]


def _pct(part: int, total: int) -> str:
    return "—" if total == 0 else f"{100 * part / total:.0f}%"


def _when(value: datetime | None) -> str:
    return "—" if value is None else f"{value:%d/%m/%Y %H:%M} UTC"


def _linked_rows(conn, start: datetime, end: datetime) -> list[tuple]:
    """Pares vigentes e não descartados cuja reclamação foi coletada na janela."""
    return conn.execute(
        f"""
        SELECT c.id, c.title, c.description, c.url, p.id, p.name, p.brand
        FROM product_complaint_match m
        JOIN product p ON p.id = m.product_id
        JOIN complaint c ON c.id = m.complaint_id
        WHERE {_LINKED} AND c.collected_at >= ? AND c.collected_at < ?
        ORDER BY c.collected_at DESC, c.id
        """,
        [start, end],
    ).fetchall()


def build_report(
    db: Database, *, period_start: datetime, period_end: datetime, control: dict
) -> str:
    """Relatório do período em Markdown. Só lê o banco."""
    previous_start = period_start - (period_end - period_start)
    analytics = AnalyticsRepository(db)
    overview = analytics.overview(DashboardFilters())
    with db.reading() as conn:
        new_total = conn.execute(
            "SELECT count(*) FROM complaint WHERE collected_at >= ? AND collected_at < ?",
            [period_start, period_end],
        ).fetchone()[0]
        current = _linked_rows(conn, period_start, period_end)
        previous = _linked_rows(conn, previous_start, period_start)
        seen_before = {
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT m.product_id FROM product_complaint_match m"
                f" JOIN complaint c ON c.id = m.complaint_id"
                f" WHERE {_LINKED} AND c.collected_at < ?",
                [period_start],
            ).fetchall()
        }
        new_matches = dict(
            conn.execute(
                "SELECT status, count(*) FROM product_complaint_match"
                " WHERE created_at >= ? AND created_at < ? GROUP BY 1",
                [period_start, period_end],
            ).fetchall()
        )

    # Triagem uma vez por reclamação; a mesma reclamação pode citar mais de um produto.
    complaints: dict[int, tuple] = {}
    by_product: dict[int, dict] = {}
    for cid, title, description, url, pid, name, brand in current:
        if cid not in complaints:
            complaints[cid] = (title, url, classify(title, description))
        entry = by_product.setdefault(
            pid, {"name": name, "brand": brand, "ids": set(), "natures": Counter()}
        )
        if cid not in entry["ids"]:
            entry["ids"].add(cid)
            entry["natures"][complaints[cid][2].nature] += 1
    previous_counts: Counter[int] = Counter()
    for _, pid in {(row[0], row[4]) for row in previous}:
        previous_counts[pid] += 1
    natures = Counter(relevance.nature for _, _, relevance in complaints.values())
    ranked = sorted(by_product.items(), key=lambda item: (-len(item[1]["ids"]), item[1]["name"]))
    linked_total = len(complaints)

    def product_name(entry: dict) -> str:
        brand = f" ({_md(entry['brand'])})" if entry["brand"] else ""
        return f"{_md(entry['name'])}{brand}"

    out = [
        f"# Relatório semanal — {period_end:%d/%m/%Y}",
        "",
        f"Período: {_when(period_start)} a {_when(period_end)}."
        " Reclamação nova = coletada pela primeira vez no período.",
        "",
        "Natureza (evento adverso, queixa técnica, comercial) é triagem por vocabulário"
        f" (`{RULES_VERSION}`): indício para leitura, não classificação final.",
        "",
        "## Resumo executivo",
        "",
    ]
    out += _table(
        ["Indicador", "Valor"],
        [
            ["Reclamações novas coletadas", new_total],
            ["… associadas a produto monitorado", linked_total],
            ["Produtos monitorados (ativos)", overview.products.active],
            ["Possíveis eventos adversos", natures[ADVERSE]],
            ["Possíveis queixas técnicas", natures[TECHNICAL]],
            ["Reclamações comerciais", natures[COMMERCIAL]],
            ["Sem sinal", natures[NO_SIGNAL]],
            ["Itens aguardando validação (total)", overview.matches.pending],
        ],
    )

    out += ["## Produtos", ""]
    if ranked:
        out += _table(
            ["Produto", "Novas", "Período anterior", "Diferença", "Participação"],
            [
                [
                    product_name(entry),
                    len(entry["ids"]),
                    previous_counts[pid],
                    f"{len(entry['ids']) - previous_counts[pid]:+d}",
                    _pct(len(entry["ids"]), linked_total),
                ]
                for pid, entry in ranked[:TOP_ROWS]
            ],
        )
        out += [
            "Diferença é numérica e depende também do que foi coletado em cada período;"
            " a seção Sinais diz quando ela sustenta afirmar alta. Uma reclamação pode"
            " citar mais de um produto, então a participação pode somar mais de 100%.",
            "",
        ]
        first_time = [entry for pid, entry in ranked if pid not in seen_before]
        if first_time:
            out += [
                "Produtos com a primeira reclamação associada neste período: "
                + ", ".join(product_name(entry) for entry in first_time),
                "",
            ]
    else:
        out += ["Nenhuma reclamação nova associada a produto no período.", ""]

    out += ["## Problemas", ""]
    signals: Counter[tuple[str, str]] = Counter()
    for _, _, relevance in complaints.values():
        for nature, terms in (
            (ADVERSE, relevance.adverse_signals),
            (TECHNICAL, relevance.technical_signals),
            (COMMERCIAL, relevance.commercial_signals),
        ):
            signals.update((_NATURE_LABELS[nature], term) for term in terms)
    if signals:
        out += ["Termos de problema mais frequentes nas reclamações novas associadas:", ""]
        out += _table(
            ["Termo", "Natureza", "Reclamações"],
            [[_md(term), nature, count] for (nature, term), count in signals.most_common(TOP_ROWS)],
        )
    if ranked:
        out += ["Natureza por produto (reincidência e concentração):", ""]
        out += _table(
            ["Produto", "Novas", *(_NATURE_LABELS[n] for n in (ADVERSE, TECHNICAL, COMMERCIAL, NO_SIGNAL))],
            [
                [
                    product_name(entry),
                    len(entry["ids"]),
                    *(entry["natures"][n] for n in (ADVERSE, TECHNICAL, COMMERCIAL, NO_SIGNAL)),
                ]
                for _, entry in ranked[:TOP_ROWS]
            ],
        )
    if not signals and not ranked:
        out += ["Sem reclamações novas associadas para analisar.", ""]

    out += _signals_section(analytics, by_product, complaints, control, period_end, period_start)
    out += _matching_section(overview.matches, new_matches, control["matching"])
    out += _pending_section(db, overview.matches.pending)
    out += _control_section(control)
    return "\n".join(out)


def _signals_section(
    analytics: AnalyticsRepository,
    by_product: dict[int, dict],
    complaints: dict[int, tuple],
    control: dict,
    period_end: datetime,
    period_start: datetime,
) -> list[str]:
    days = max(1, round((period_end - period_start).total_seconds() / 86400))
    out = [
        "## Sinais",
        "",
        "Critérios (nada fora deles é chamado de sinal):",
        f"1. **Alta**: reclamações do produto por data de publicação, janela de {days} dias"
        " contra a anterior, com busca nas duas janelas e diferença significativa"
        " (teste exato, p < 0,05) — o mesmo teste do dashboard;",
        f"2. **Reincidência**: {ADVERSE_REINCIDENCE_MIN}+ reclamações novas do mesmo produto"
        " com indício de evento adverso no período.",
        "",
    ]
    found: list[str] = []
    for pid, entry in by_product.items():
        trend = analytics.trend(DashboardFilters(product_id=pid), days=days)
        if trend.direction == "alta":
            found.append(
                f"- Alta — {_md(entry['name'])}: {trend.current.complaints} contra"
                f" {trend.previous.complaints} (p={trend.p_value:.3f})."
            )
        if entry["natures"][ADVERSE] >= ADVERSE_REINCIDENCE_MIN:
            found.append(
                f"- Reincidência — {_md(entry['name'])}: {entry['natures'][ADVERSE]}"
                " reclamações novas com indício de evento adverso."
            )
    out += found or ["Nenhum produto atende aos critérios neste período."]
    out.append("")

    adverse = [
        (title, url, relevance)
        for title, url, relevance in complaints.values()
        if relevance.nature == ADVERSE
    ]
    if adverse:
        out += ["Reclamações novas com indício de evento adverso (leitura humana recomendada):", ""]
        out += _table(
            ["Reclamação", "Termos", "Fonte"],
            [
                [_md(title, 100), _md(", ".join(relevance.adverse_signals)), _link(url)]
                for title, url, relevance in adverse[:TOP_ROWS]
            ],
        )
        if len(adverse) > TOP_ROWS:
            out += [f"… e mais {len(adverse) - TOP_ROWS} na aba de validação.", ""]
    return out


def _matching_section(matches, new_matches: dict, run: dict) -> list[str]:
    human = Counter()
    for row in matches.review:
        human[row.human] += row.pairs
    out = ["## Qualidade do matching", ""]
    out += _table(
        ["Indicador", "Valor"],
        [
            ["Matches novos no período (confirmados / possíveis)",
             f"{new_matches.get('confirmed', 0)} / {new_matches.get('possible', 0)}"],
            ["Matches vigentes (total)", matches.current],
            ["Confirmados / possíveis / descartados (decisão que vale)",
             f"{matches.confirmed} / {matches.possible} / {matches.discarded}"],
            ["Revisados por humano", matches.reviewed],
            ["Confirmações humanas", human[MatchStatus.CONFIRMED]],
            ["Rejeições humanas", human[MatchStatus.DISCARDED]],
            ["Dúvidas humanas", human[MatchStatus.POSSIBLE]],
            ["Obsoletos (sem evidência vigente)", matches.stale],
            ["Precisão da confirmação automática",
             "— (há confirmados ainda não revisados)" if matches.precision is None
             else f"{100 * matches.precision:.1f}%"],
            ["Matching desta execução (criados / atualizados / obsoletos)",
             f"{run.get('created', 0)} / {run.get('updated', 0)} / {run.get('stale', 0)}"],
            ["Versão do motor", run.get("matcher_version") or "—"],
        ],
    )
    return out


def _pending_section(db: Database, pending: int) -> list[str]:
    out = ["## Pendências", "", f"{pending} par(es) aguardando validação humana.", ""]
    queue = MatchRepository(db).queue(limit=TOP_ROWS)
    if queue:
        out += _table(
            ["Score", "Produto", "Reclamação", "Fonte"],
            [
                [f"{item.match.score:.2f}", _md(item.product.name), _md(item.complaint.title, 100),
                 _link(item.complaint.url)]
                for item in queue
            ],
        )
        if pending > len(queue):
            out += [f"… e mais {pending - len(queue)} na aba de validação.", ""]
    return out


def _control_section(control: dict) -> list[str]:
    products = control["products"]
    failed = [p for p in products if p["status"] == "failed"]
    out = ["## Controle de execução", ""]
    out += _table(
        ["Campo", "Valor"],
        [
            ["Execução", control["run_id"]],
            ["Status", control["status"]],
            ["Fonte", control["source"]],
            ["Início", _when(control["started_at"])],
            ["Coleta", "sim" if control["collect"] else "não (só relatório)"],
            ["Produtos pesquisados", len(products)],
            ["Produtos com falha", len(failed)],
            ["Buscas (termos)", control["search_runs"]],
            ["Reclamações coletadas / novas", f"{control['collected']} / {control['inserted']}"],
        ],
    )
    if control["errors"]:
        out += ["Erros:", ""] + [f"- {_md(error, 300)}" for error in control["errors"]] + [""]
    return out


# ------------------------------------------------------------------ linha de comando


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.monitor",
        description="Coleta todos os produtos ativos, executa o matching e gera o relatório.",
    )
    parser.add_argument("--dias", type=int, default=7, help="período do relatório (padrão 7)")
    parser.add_argument(
        "--paginas", type=int, default=1, choices=range(1, 11), metavar="1-10",
        help="páginas de resultado por termo (padrão 1)",
    )
    parser.add_argument(
        "--sem-coleta", action="store_true", help="não coleta; só gera o relatório do período"
    )
    args = parser.parse_args(argv)
    if args.dias < 1:
        parser.error("--dias deve ser 1 ou mais")

    settings = get_settings()
    db = Database(settings.db_path)
    try:
        db.connect()
    except duckdb.IOException as exc:
        print(
            "Banco em uso por outro processo — provavelmente o servidor está aberto."
            f" Feche-o e rode de novo.\n{exc}",
            file=sys.stderr,
        )
        return 2
    try:
        outcome = asyncio.run(
            run_monitor(
                db,
                ReclameAquiCollector(),
                report_dir=settings.data_dir / "reports",
                days=args.dias,
                pages=args.paginas,
                collect=not args.sem_coleta,
            )
        )
    finally:
        db.close()

    print(
        f"execução {outcome.run_id}: {outcome.status} — {outcome.products_searched} produto(s),"
        f" {outcome.search_runs} busca(s), {outcome.collected} coletada(s),"
        f" {outcome.inserted} nova(s)"
    )
    for error in outcome.errors:
        print(f"  erro: {error}")
    if outcome.report_path is not None:
        print(f"relatório: {outcome.report_path}")
    return 0 if outcome.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
