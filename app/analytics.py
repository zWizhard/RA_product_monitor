"""
Métricas do dashboard, contadas pelo banco.

Regras que ficam aqui, e não na API nem no frontend:
- toda métrica é agregação SQL sobre dados gravados; nada é estimado, extrapolado ou
  descrito por LLM. Quem quiser interpretar o resultado recebe o número já calculado;
- a decisão que vale é a humana quando existe, e um par datado como obsoleto
  (`stale_since`) não conta como correspondência vigente — as duas regras vêm de
  `app.match_repository`, não são reescritas aqui;
- a data de referência da análise é a **publicação** da reclamação, não a coleta: é
  quando o fato relatado aconteceu. Reclamação sem data legível não entra em série
  temporal nenhuma, e é contada à parte para que a lacuna fique visível;
- variação entre períodos só recebe direção quando passa dois testes explícitos: a
  coleta alcançou as duas janelas e a diferença não é compatível com oscilação.
  Enquanto não passa, existe diferença numérica e nenhuma tendência a afirmar.

O que as contagens **não** medem: o universo de reclamações do Reclame Aqui. A coleta é
dirigida por termo de busca, então o volume de um período depende também de quando e com
quais termos se coletou. É por isso que a comparação entre janelas checa antes se cada
uma teve busca própria: sem isso, um período coletado depois aparece como crescimento
do fenômeno quando é só o recorte da coleta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import comb

from app.db import Database
from app.match_repository import current_clause, decision_sql, pending_clause
from app.matching import MatchStatus
from app.models import (
    CategoryCount,
    ComplaintMetrics,
    DashboardFiltersEcho,
    DashboardOverview,
    Granularity,
    MatchMetrics,
    ProductComplaintCount,
    ProductMetrics,
    SearchMetrics,
    StatusCount,
    Timeline,
    TimelineBucket,
    Trend,
    TrendWindow,
)
from app.repository import utc_now
from app.taxonomy import Category, canonical_subcategory

# Correspondência que conta como citação do produto: vigente e não descartada.
_LINKED = f"{current_clause('m.')} AND {decision_sql('m.')} <> '{MatchStatus.DISCARDED}'"
# Duas janelas com menos reclamações que isto, somadas, não sustentam comparação:
# a diferença observada é compatível com acaso qualquer que seja o resultado do teste.
MIN_TREND_SAMPLE = 10
# Limiar do teste de variação. Bilateral: o dashboard não assume de antemão que o
# volume deveria subir ou descer.
TREND_ALPHA = 0.05


def _midnight(day: date) -> str:
    """Instante SQL do início do dia.

    O valor é um `date` já validado pelo Pydantic e formatado aqui — nunca texto do
    usuário —, por isso pode entrar na consulta como literal.
    """
    return f"TIMESTAMP '{day.isoformat()} 00:00:00'"


@dataclass(frozen=True)
class DashboardFilters:
    """Escopo de uma consulta de métricas: produto/categoria e recorte temporal.

    O mesmo escopo atravessa todos os endpoints do dashboard, de modo que os números
    exibidos juntos se refiram sempre ao mesmo recorte.
    """

    product_id: int | None = None
    category: Category | None = None
    subcategory: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    @property
    def scopes_products(self) -> bool:
        """O escopo restringe o conjunto de produtos."""
        return (
            self.product_id is not None
            or self.category is not None
            or canonical_subcategory(self.subcategory) is not None
        )

    def product_conditions(self, prefix: str = "") -> tuple[list[str], list[object]]:
        """Condições sobre as colunas de `product`, com os parâmetros na mesma ordem."""
        where: list[str] = []
        params: list[object] = []
        if self.product_id is not None:
            where.append(f"{prefix}id = ?")
            params.append(self.product_id)
        if self.category is not None:
            where.append(f"{prefix}category = ?")
            params.append(str(self.category))
        canonical = canonical_subcategory(self.subcategory)
        if canonical is not None:
            where.append(f"{prefix}subcategory = ?")
            params.append(canonical)
        return where, params

    def period_sql(self, column: str) -> str:
        """Recorte temporal sobre uma coluna de data. `TRUE` quando não há recorte.

        Meio-aberto no fim (`< date_to + 1 dia`) para que `date_to` seja um dia
        inteiro, e não o seu instante zero.
        """
        bounds: list[str] = []
        if self.date_from is not None:
            bounds.append(f"{column} >= {_midnight(self.date_from)}")
        if self.date_to is not None:
            bounds.append(f"{column} < {_midnight(self.date_to + timedelta(days=1))}")
        return " AND ".join(bounds) if bounds else "TRUE"

    def complaint_scope(self) -> tuple[str, list[object]]:
        """Reclamação dentro do escopo de produtos.

        Sem filtro de produto, é toda reclamação coletada — inclusive a que não casou
        com nada, que também é resultado da operação. Com filtro, exige
        correspondência vigente e não descartada a um produto do filtro: a relação
        auditável `product <-> complaint` é o que autoriza dizer que a reclamação é
        daquele produto.
        """
        if not self.scopes_products:
            return "TRUE", []
        return self.complaint_has_match()

    def complaint_has_match(
        self, decision: MatchStatus | None = None
    ) -> tuple[str, list[object]]:
        """Reclamação com correspondência vigente a um produto do escopo.

        Com `decision`, exige que a decisão que vale seja aquela.
        """
        where, params = self.product_conditions("p.")
        clause = _LINKED
        if decision is not None:
            clause = f"{current_clause('m.')} AND {decision_sql('m.')} = '{decision}'"
        extra = f" AND {' AND '.join(where)}" if where else ""
        return (
            "EXISTS (SELECT 1 FROM product_complaint_match m"
            " JOIN product p ON p.id = m.product_id"
            f" WHERE m.complaint_id = c.id AND {clause}{extra})",
            params,
        )

    def echo(self) -> DashboardFiltersEcho:
        """O escopo como o backend o entendeu, para o cliente exibir o recorte real."""
        return DashboardFiltersEcho(
            product_id=self.product_id,
            category=self.category,
            subcategory=canonical_subcategory(self.subcategory),
            date_from=self.date_from,
            date_to=self.date_to,
        )


def compare_counts(current: int, previous: int) -> tuple[float, bool, str | None]:
    """Compara duas contagens de janelas de igual duração.

    Devolve `(p_value, significant, note)`. O teste é o exato condicional: dadas duas
    contagens de Poisson com exposição igual, a atual, condicionada ao total das duas,
    segue Binomial(n, 1/2) sob a hipótese de mesma taxa. É exato para contagem pequena,
    não depende de aproximação normal e não precisa de dependência nova.

    `note` explica por que a diferença não é conclusiva; é `None` quando ela é.
    """
    total = current + previous
    if total < MIN_TREND_SAMPLE:
        return (
            1.0,
            False,
            f"amostra insuficiente: {total} reclamações nas duas janelas"
            f" (mínimo {MIN_TREND_SAMPLE})",
        )
    # Cauda bilateral do caso simétrico p=1/2: dobrar a cauda menor.
    tail = sum(comb(total, i) for i in range(min(current, previous) + 1)) / 2**total
    p_value = min(1.0, 2 * tail)
    if p_value >= TREND_ALPHA:
        return (
            p_value,
            False,
            f"variação compatível com oscilação: p={p_value:.3f}"
            f" (limiar {TREND_ALPHA})",
        )
    return p_value, True, None


class AnalyticsRepository:
    """Leitura agregada para o dashboard. Não escreve nada."""

    def __init__(self, db: Database):
        self._db = db

    # ---------------------------------------------------------------- contadores

    def overview(self, filters: DashboardFilters) -> DashboardOverview:
        return DashboardOverview(
            filters=filters.echo(),
            products=self._products(filters),
            complaints=self._complaints(filters),
            matches=self._matches(filters),
            searches=self._searches(filters),
        )

    def _products(self, filters: DashboardFilters) -> ProductMetrics:
        """Produtos monitorados. Ignora o recorte temporal: produto não é um evento."""
        where, params = filters.product_conditions("p.")
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._db.reading() as conn:
            total, active = conn.execute(
                "SELECT count(*), count(*) FILTER (WHERE p.active)"
                f" FROM product p{clause}",
                params,
            ).fetchone()
            rows = conn.execute(
                "SELECT p.category, p.subcategory, count(*),"
                " count(*) FILTER (WHERE p.active)"
                f" FROM product p{clause} GROUP BY 1, 2 ORDER BY 1, 2",
                params,
            ).fetchall()
        return ProductMetrics(
            total=total,
            active=active,
            by_category=[
                CategoryCount(
                    category=Category(row[0]), subcategory=row[1], total=row[2], active=row[3]
                )
                for row in rows
            ],
        )

    def _complaints(self, filters: DashboardFilters) -> ComplaintMetrics:
        scope, scope_params = filters.complaint_scope()
        linked, linked_params = filters.complaint_has_match()
        period = filters.period_sql("c.published_at")
        with self._db.reading() as conn:
            # Ordem dos parâmetros = ordem em que os `?` aparecem no texto: primeiro os
            # do `EXISTS` da coluna `linked`, depois os do escopo no `WHERE`.
            row = conn.execute(
                f"""
                SELECT count(*) FILTER (WHERE {period}),
                       count(*) FILTER (WHERE c.published_at IS NULL),
                       count(*) FILTER (WHERE {period} AND {linked}),
                       min(c.published_at) FILTER (WHERE {period}),
                       max(c.published_at) FILTER (WHERE {period}),
                       min(c.collected_at) FILTER (WHERE {period}),
                       max(c.collected_at) FILTER (WHERE {period})
                FROM complaint c WHERE {scope}
                """,
                [*linked_params, *scope_params],
            ).fetchone()
            status_rows = conn.execute(
                f"SELECT c.status, count(*) FROM complaint c"
                f" WHERE {scope} AND {period} GROUP BY 1 ORDER BY 2 DESC, 1",
                scope_params,
            ).fetchall()
        return ComplaintMetrics(
            total=row[0],
            undated=row[1],
            linked=row[2],
            first_published=row[3],
            last_published=row[4],
            first_collected=row[5],
            last_collected=row[6],
            by_status=[StatusCount(status=name, complaints=count) for name, count in status_rows],
        )

    def _matches(self, filters: DashboardFilters) -> MatchMetrics:
        where, params = filters.product_conditions("p.")
        period = filters.period_sql("c.published_at")
        conditions = [*where, period]
        decision = decision_sql("m.")
        current = current_clause("m.")
        with self._db.reading() as conn:
            row = conn.execute(
                f"""
                SELECT count(*),
                       count(*) FILTER (WHERE {current}),
                       count(*) FILTER (WHERE NOT ({current})),
                       count(*) FILTER (WHERE {current} AND {decision} = 'confirmed'),
                       count(*) FILTER (WHERE {current} AND {decision} = 'possible'),
                       count(*) FILTER (WHERE {current} AND {decision} = 'discarded'),
                       count(*) FILTER (WHERE m.reviewed_status IS NOT NULL),
                       count(*) FILTER (WHERE {pending_clause('m.')})
                FROM product_complaint_match m
                JOIN product p ON p.id = m.product_id
                JOIN complaint c ON c.id = m.complaint_id
                WHERE {' AND '.join(conditions)}
                """,
                params,
            ).fetchone()
        return MatchMetrics(
            total=row[0],
            current=row[1],
            stale=row[2],
            confirmed=row[3],
            possible=row[4],
            discarded=row[5],
            reviewed=row[6],
            pending=row[7],
        )

    @staticmethod
    def _search_scope(filters: DashboardFilters) -> tuple[str, list[object]]:
        """Busca dentro do escopo de produtos. Busca avulsa só entra sem filtro."""
        if not filters.scopes_products:
            return "TRUE", []
        where, params = filters.product_conditions("p.")
        return (
            "EXISTS (SELECT 1 FROM product p WHERE p.id = r.product_id"
            f" AND {' AND '.join(where)})",
            params,
        )

    def _searches(self, filters: DashboardFilters) -> SearchMetrics:
        """Execuções de busca. O recorte se aplica à data de execução, não à publicação."""
        scope, params = self._search_scope(filters)
        conditions = [filters.period_sql("r.started_at"), scope]
        with self._db.reading() as conn:
            row = conn.execute(
                """
                SELECT count(*), count(*) FILTER (WHERE r.status = 'completed'),
                       count(*) FILTER (WHERE r.status = 'failed'),
                       coalesce(sum(r.inserted), 0), max(r.started_at)
                FROM search_run r
                """
                f" WHERE {' AND '.join(conditions)}",
                params,
            ).fetchone()
        return SearchMetrics(
            runs=row[0], completed=row[1], failed=row[2], inserted=row[3], last_run_at=row[4]
        )

    # ------------------------------------------------------------ produtos citados

    def top_products(
        self, filters: DashboardFilters, limit: int = 10
    ) -> list[ProductComplaintCount]:
        """Produtos mais citados no escopo, por reclamações distintas associadas.

        Só entra produto com ao menos uma correspondência vigente e não descartada:
        a lista responde "quem está sendo citado", não "quem está cadastrado".
        """
        where, params = filters.product_conditions("p.")
        conditions = [*where, filters.period_sql("c.published_at")]
        decision = decision_sql("m.")
        with self._db.reading() as conn:
            rows = conn.execute(
                f"""
                SELECT p.id, p.name, p.brand, p.category, p.subcategory,
                       count(DISTINCT m.complaint_id) FILTER (WHERE {_LINKED}),
                       count(DISTINCT m.complaint_id)
                           FILTER (WHERE {current_clause('m.')} AND {decision} = 'confirmed'),
                       count(DISTINCT m.complaint_id)
                           FILTER (WHERE {current_clause('m.')} AND {decision} = 'possible'),
                       count(*) FILTER (WHERE {pending_clause('m.')})
                FROM product p
                JOIN product_complaint_match m ON m.product_id = p.id
                JOIN complaint c ON c.id = m.complaint_id
                WHERE {' AND '.join(conditions)}
                GROUP BY 1, 2, 3, 4, 5
                HAVING count(DISTINCT m.complaint_id) FILTER (WHERE {_LINKED}) > 0
                ORDER BY 6 DESC, 7 DESC, p.name
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [
            ProductComplaintCount(
                product_id=row[0],
                name=row[1],
                brand=row[2],
                category=Category(row[3]),
                subcategory=row[4],
                complaints=row[5],
                confirmed=row[6],
                possible=row[7],
                pending=row[8],
            )
            for row in rows
        ]

    # ------------------------------------------------------------ série temporal

    def timeline(
        self, filters: DashboardFilters, granularity: Granularity = Granularity.MONTH
    ) -> Timeline:
        """Reclamações por passo de tempo, pela data de publicação.

        Duas séries: as reclamações do escopo e, dentro delas, as que têm
        correspondência confirmada — a diferença entre as duas é o que ainda não foi
        confirmado, e não some do gráfico.
        """
        scope, scope_params = filters.complaint_scope()
        confirmed, confirmed_params = filters.complaint_has_match(MatchStatus.CONFIRMED)
        period = filters.period_sql("c.published_at")
        with self._db.reading() as conn:
            rows = conn.execute(
                f"""
                SELECT date_trunc('{granularity}', c.published_at) AS bucket,
                       count(*), count(*) FILTER (WHERE {confirmed})
                FROM complaint c
                WHERE c.published_at IS NOT NULL AND {scope} AND {period}
                GROUP BY 1 ORDER BY 1
                """,
                [*confirmed_params, *scope_params],
            ).fetchall()
            undated = conn.execute(
                f"SELECT count(*) FROM complaint c"
                f" WHERE c.published_at IS NULL AND {scope}",
                scope_params,
            ).fetchone()[0]
        counted = {row[0].date(): (row[1], row[2]) for row in rows}
        buckets = [
            TimelineBucket(
                period_start=day,
                complaints=counted.get(day, (0, 0))[0],
                confirmed=counted.get(day, (0, 0))[1],
            )
            for day in _bucket_starts(sorted(counted), granularity)
        ]
        return Timeline(
            granularity=granularity, filters=filters.echo(), buckets=buckets, undated=undated
        )

    def trend(self, filters: DashboardFilters, days: int = 90) -> Trend:
        """Compara a janela de `days` dias que termina na coleta mais recente.

        `date_from` não participa: as duas janelas precisam ter a mesma duração para
        que a comparação signifique algo, e quem define a duração é `days`.

        O fim da janela é `date_to` (ou hoje), **limitado à última coleta**: um dia
        posterior à última busca não tem reclamação nenhuma no banco por construção —
        ninguém foi lá buscar —, e contá-lo derrubaria o período recente como se
        tivesse havido queda. A janela efetivamente usada volta em `current`.

        Duas condições, checadas em separado, autorizam falar de tendência: cada
        janela ter tido busca própria (`comparable`) e a diferença não ser compatível
        com oscilação (`significant`). Faltando qualquer uma, a resposta traz os
        números e nenhuma direção.
        """
        scope, scope_params = filters.complaint_scope()
        searches, search_params = self._search_scope(filters)
        with self._db.reading() as conn:
            end = self._collectable_end(conn, filters, searches, search_params)
            current_start = end - timedelta(days=days - 1)
            previous_end = current_start - timedelta(days=1)
            previous_start = current_start - timedelta(days=days)
            current = self._count_published(conn, scope, scope_params, current_start, end)
            previous = self._count_published(
                conn, scope, scope_params, previous_start, previous_end
            )
            current_runs = self._count_searches(
                conn, searches, search_params, current_start, end
            )
            previous_runs = self._count_searches(
                conn, searches, search_params, previous_start, previous_end
            )
        p_value, significant, note = compare_counts(current, previous)
        # Exposição: uma janela sem busca própria só foi vista por uma coleta posterior.
        # Comparar volumes nessa condição mede também quando se coletou — e a coleta é
        # dirigida por termo, então o resultado favorece o período mais recente.
        comparable = current_runs > 0 and previous_runs > 0
        # As duas condições falham de forma independente: quando as duas falham, a nota
        # nomeia as duas. A cobertura vem primeiro por ser a objeção mais forte — sem
        # ela, nem faria sentido discutir o tamanho da amostra.
        reasons: list[str] = []
        if not comparable:
            reasons.append(
                "coleta não cobriu as duas janelas (buscas: atual"
                f" {current_runs}, anterior {previous_runs}): a diferença pode ser"
                " efeito de quando se coletou"
            )
        if note is not None:
            reasons.append(note)
        note = "; ".join(reasons) or None
        delta = current - previous
        direction = None
        if significant and comparable:
            direction = "alta" if delta > 0 else "queda"
        return Trend(
            days=days,
            filters=filters.echo(),
            current=TrendWindow(
                start=current_start, end=end, complaints=current, searches=current_runs
            ),
            previous=TrendWindow(
                start=previous_start,
                end=previous_end,
                complaints=previous,
                searches=previous_runs,
            ),
            delta=delta,
            delta_pct=None if previous == 0 else round(100 * delta / previous, 1),
            p_value=round(p_value, 6),
            significant=significant,
            comparable=comparable,
            direction=direction,
            basis=(
                "reclamações do escopo por data de publicação, em janelas que terminam"
                " na última coleta, contra buscas executadas em cada janela"
            ),
            note=note,
        )

    @staticmethod
    def _collectable_end(
        conn, filters: DashboardFilters, scope: str, params: list[object]
    ) -> date:
        """Fim de janela que a coleta alcança: o pedido, limitado à última busca.

        Sem busca alguma não há o que limitar — e, sem busca, a comparação já não é
        considerada comparável.
        """
        requested = filters.date_to or utc_now().date()
        last = conn.execute(
            f"SELECT max(r.started_at) FROM search_run r WHERE {scope}", params
        ).fetchone()[0]
        return requested if last is None else min(requested, last.date())

    @staticmethod
    def _count_published(conn, scope: str, params: list[object], start: date, end: date) -> int:
        return conn.execute(
            f"SELECT count(*) FROM complaint c WHERE {scope}"
            f" AND c.published_at >= {_midnight(start)}"
            f" AND c.published_at < {_midnight(end + timedelta(days=1))}",
            params,
        ).fetchone()[0]

    @staticmethod
    def _count_searches(conn, scope: str, params: list[object], start: date, end: date) -> int:
        """Execuções de busca iniciadas na janela: a exposição que ela teve."""
        return conn.execute(
            f"SELECT count(*) FROM search_run r WHERE {scope}"
            f" AND r.started_at >= {_midnight(start)}"
            f" AND r.started_at < {_midnight(end + timedelta(days=1))}",
            params,
        ).fetchone()[0]


def _step(day: date, granularity: Granularity) -> date:
    """Início do passo seguinte."""
    if granularity is Granularity.DAY:
        return day + timedelta(days=1)
    if granularity is Granularity.WEEK:
        return day + timedelta(days=7)
    year, month = divmod(day.month, 12)
    return date(day.year + year, month + 1, 1)


def _bucket_starts(found: list[date], granularity: Granularity) -> list[date]:
    """Todos os passos entre o primeiro e o último com reclamação, inclusive os vazios.

    Sem isto, um gráfico de dois pontos distantes desenharia uma linha contínua sobre
    meses em que não houve reclamação nenhuma.
    """
    if not found:
        return []
    days = [found[0]]
    while days[-1] < found[-1]:
        days.append(_step(days[-1], granularity))
    return days
