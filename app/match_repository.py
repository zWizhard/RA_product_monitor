"""
Persistência da relação produto ↔ reclamação.

Regras que ficam aqui, e não na API:
- um par (produto, reclamação) tem no máximo uma linha: reprocessar atualiza, nunca
  duplica;
- reprocessar reescreve apenas a decisão automática (método, score, evidência); a
  decisão humana e sua justificativa permanecem intactas;
- match não é apagado: revisão humana o marca como `discarded`, preservando a
  evidência que levou à associação;
- decisão automática reescrita não some: a anterior é copiada para
  `product_complaint_match_revision` antes, com a versão do motor que a produziu.
  Trocar de `matcher_version` é, por isso, auditável e não destrutivo.
"""

from __future__ import annotations

from datetime import datetime

from app.db import Database
from app.matching import MatchCandidate, MatchStatus
from app.models import (
    MatchRevision,
    MatchReviewItem,
    ProductComplaintMatch,
    ReviewComplaint,
    ReviewProduct,
)
from app.repository import RecordNotFound, utc_now

# Ordem em que as colunas do match são lidas; `_to_match` depende dela.
_MATCH_FIELDS = (
    "id",
    "product_id",
    "complaint_id",
    "method",
    "score",
    "matched_term",
    "evidence_field",
    "evidence",
    "explanation",
    "status",
    "matcher_version",
    "reviewed_status",
    "reviewed_by",
    "reviewed_at",
    "review_note",
    "stale_since",
    "stale_reason",
    "shadowed_by_product_id",
    "created_at",
    "updated_at",
)
_MATCH_COLUMNS = ", ".join(_MATCH_FIELDS)
# Colunas copiadas para o histórico; nome da coluna = nome do campo em `MatchRevision`.
_REVISION_FIELDS = (
    "id",
    "match_id",
    "product_id",
    "complaint_id",
    "method",
    "score",
    "matched_term",
    "evidence_field",
    "evidence",
    "explanation",
    "status",
    "matcher_version",
    "shadowed_by_product_id",
    "stale_since",
    "stale_reason",
    "decided_at",
    "superseded_at",
)
# Colunas da fila de revisão: nome da coluna = nome do campo em `ReviewProduct`/`ReviewComplaint`.
_PRODUCT_FIELDS = (
    "id",
    "name",
    "category",
    "subcategory",
    "brand",
    "manufacturer",
    "model",
    "active",
)
_COMPLAINT_FIELDS = (
    "id",
    "source",
    "url",
    "title",
    "description",
    "company",
    "location",
    "published_at",
    "collected_at",
)


def _columns(prefix: str, fields: tuple[str, ...]) -> str:
    return ", ".join(f"{prefix}{field}" for field in fields)


def decision_sql(prefix: str = "") -> str:
    """Decisão que vale, em SQL: a humana quando existe, a automática caso contrário."""
    return f"coalesce({prefix}reviewed_status, {prefix}status)"


def current_clause(prefix: str = "") -> str:
    """Par vigente: não foi datado como obsoleto, logo a evidência continua valendo."""
    return f"{prefix}stale_since IS NULL"


def pending_clause(prefix: str = "") -> str:
    """Par que espera decisão humana: vigente e com a decisão que vale ainda em `possible`."""
    return f"{current_clause(prefix)} AND {decision_sql(prefix)} = 'possible'"


# Comparação de score em ponto flutuante: diferença menor que isto é a mesma decisão.
_SCORE_EPSILON = 1e-9


class MatchNotFound(RecordNotFound):
    """Match inexistente."""


class MatchRepository:
    def __init__(self, db: Database):
        self._db = db

    # ---------------------------------------------------------------- leitura

    def get(self, match_id: int) -> ProductComplaintMatch:
        with self._db.reading() as conn:
            row = conn.execute(
                f"SELECT {_MATCH_COLUMNS} FROM product_complaint_match WHERE id = ?",
                [match_id],
            ).fetchone()
        if row is None:
            raise MatchNotFound(f"match {match_id} não encontrado")
        return self._to_match(row)

    def list(
        self,
        *,
        product_id: int | None = None,
        complaint_id: int | None = None,
        decision: MatchStatus | None = None,
        method: str | None = None,
        reviewed: bool | None = None,
        stale: bool | None = None,
        pending: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProductComplaintMatch]:
        """`decision` filtra pela decisão que vale: a humana quando existe."""
        where: list[str] = []
        params: list[object] = []
        if product_id is not None:
            where.append("product_id = ?")
            params.append(product_id)
        if complaint_id is not None:
            where.append("complaint_id = ?")
            params.append(complaint_id)
        if decision is not None:
            where.append(f"{decision_sql()} = ?")
            params.append(str(decision))
        if method is not None:
            where.append("method = ?")
            params.append(method)
        if reviewed is not None:
            where.append(
                "reviewed_status IS NOT NULL" if reviewed else "reviewed_status IS NULL"
            )
        if stale is not None:
            where.append("stale_since IS NOT NULL" if stale else "stale_since IS NULL")
        if pending is not None:
            pending_sql = pending_clause()
            where.append(pending_sql if pending else f"NOT ({pending_sql})")
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._db.reading() as conn:
            rows = conn.execute(
                f"SELECT {_MATCH_COLUMNS} FROM product_complaint_match{clause}"
                " ORDER BY score DESC, id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._to_match(row) for row in rows]

    def queue(
        self,
        *,
        product_id: int | None = None,
        category: str | None = None,
        pending: bool | None = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MatchReviewItem]:
        """Fila de revisão: o par com o produto candidato e a reclamação ao lado.

        Por padrão devolve só o que espera decisão humana, do score mais alto para o
        mais baixo. `pending=None` traz também o que já foi decidido, para reexame.
        """
        where: list[str] = []
        params: list[object] = []
        if product_id is not None:
            where.append("m.product_id = ?")
            params.append(product_id)
        if category is not None:
            where.append("p.category = ?")
            params.append(str(category))
        if pending is not None:
            pending_sql = pending_clause("m.")
            where.append(pending_sql if pending else f"NOT ({pending_sql})")
        filters = f" WHERE {' AND '.join(where)}" if where else ""
        with self._db.reading() as conn:
            rows = conn.execute(
                f"SELECT {_columns('m.', _MATCH_FIELDS)},"
                f" {_columns('p.', _PRODUCT_FIELDS)}, {_columns('c.', _COMPLAINT_FIELDS)}"
                " FROM product_complaint_match m"
                " JOIN product p ON p.id = m.product_id"
                " JOIN complaint c ON c.id = m.complaint_id"
                f"{filters} ORDER BY m.score DESC, m.id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        cut = len(_MATCH_FIELDS)
        split = cut + len(_PRODUCT_FIELDS)
        return [
            MatchReviewItem(
                match=self._to_match(row[:cut]),
                product=ReviewProduct(**dict(zip(_PRODUCT_FIELDS, row[cut:split], strict=True))),
                complaint=ReviewComplaint(
                    **dict(zip(_COMPLAINT_FIELDS, row[split:], strict=True))
                ),
            )
            for row in rows
        ]

    # ----------------------------------------------------------------- escrita

    def save(
        self,
        *,
        product_id: int,
        complaint_id: int,
        candidate: MatchCandidate,
        status: MatchStatus,
        matcher_version: str,
        now: datetime | None = None,
    ) -> str:
        """Grava o candidato. Devolve `created`, `updated` ou `unchanged`.

        `unchanged` é o caso normal do reprocessamento: mesma entrada, mesma decisão,
        nem sequer `updated_at` avança.
        """
        moment = now or utc_now()
        values = (
            str(candidate.method),
            candidate.score,
            candidate.matched_term,
            candidate.evidence_field,
            candidate.evidence,
            candidate.explanation,
            str(status),
            matcher_version,
            candidate.shadowed_by_product_id,
        )
        with self._db.transaction() as conn:
            row = conn.execute(
                """
                SELECT id, method, score, matched_term, evidence_field, evidence,
                       explanation, status, matcher_version, shadowed_by_product_id,
                       stale_since, stale_reason, updated_at
                FROM product_complaint_match WHERE product_id = ? AND complaint_id = ?
                """,
                [product_id, complaint_id],
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO product_complaint_match (
                        product_id, complaint_id, method, score, matched_term,
                        evidence_field, evidence, explanation, status, matcher_version,
                        shadowed_by_product_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [product_id, complaint_id, *values, moment, moment],
                )
                return "created"
            match_id, current = row[0], tuple(row[1:-3])
            was_stale = row[-3] is not None
            same_score = abs(current[1] - candidate.score) < _SCORE_EPSILON
            same = current[0] == values[0] and same_score and current[2:] == values[2:]
            if same and not was_stale:
                return "unchanged"
            # A decisão automática vai ser reescrita: a anterior é preservada antes,
            # com a versão do motor que a tomou.
            conn.execute(
                """
                INSERT INTO product_complaint_match_revision (
                    match_id, product_id, complaint_id, method, score, matched_term,
                    evidence_field, evidence, explanation, status, matcher_version,
                    shadowed_by_product_id, stale_since, stale_reason, decided_at,
                    superseded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [match_id, product_id, complaint_id, *current, row[-3], row[-2], row[-1], moment],
            )
            # Evidência reencontrada: o par volta a ser vigente.
            conn.execute(
                """
                UPDATE product_complaint_match SET method = ?, score = ?, matched_term = ?,
                    evidence_field = ?, evidence = ?, explanation = ?, status = ?,
                    matcher_version = ?, shadowed_by_product_id = ?, stale_since = NULL,
                    stale_reason = NULL, updated_at = ?
                WHERE id = ?
                """,
                [*values, moment, match_id],
            )
        return "updated"

    def revisions(self, match_id: int) -> list[MatchRevision]:
        """Decisões automáticas anteriores deste par, da mais antiga para a mais recente."""
        with self._db.reading() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_REVISION_FIELDS)} FROM product_complaint_match_revision"
                " WHERE match_id = ? ORDER BY id",
                [match_id],
            ).fetchall()
        return [MatchRevision(**dict(zip(_REVISION_FIELDS, row, strict=True))) for row in rows]

    def pairs_for_products(self, product_ids: list[int]) -> set[tuple[int, int]]:
        """Pares já gravados para estes produtos, para detectar os que perderam evidência."""
        if not product_ids:
            return set()
        placeholders = ", ".join("?" for _ in product_ids)
        with self._db.reading() as conn:
            rows = conn.execute(
                "SELECT product_id, complaint_id FROM product_complaint_match"
                f" WHERE product_id IN ({placeholders})",
                product_ids,
            ).fetchall()
        return {(row[0], row[1]) for row in rows}

    def mark_stale(
        self,
        pairs: dict[tuple[int, int], str] | set[tuple[int, int]],
        now: datetime | None = None,
    ) -> int:
        """Data como obsoletos os pares que deixaram de ter evidência nesta execução.

        Não apaga nada: a evidência que sustentou a associação, a decisão automática que
        a acompanhava e a `matcher_version` que a produziu continuam na linha, e a decisão
        humana também. Quem já estava datado não é redatado.

        `pairs` pode trazer, por par, o motivo — "sem evidência" ou a regra de arbitragem
        que o suprimiu. É o que distingue "o motor não achou mais" de "o motor decidiu
        que este par não devia existir".
        """
        if not pairs:
            return 0
        motivos = pairs if isinstance(pairs, dict) else dict.fromkeys(pairs, "sem evidência")
        moment = now or utc_now()
        marked = 0
        with self._db.transaction() as conn:
            for (product_id, complaint_id), motivo in sorted(motivos.items()):
                row = conn.execute(
                    """
                    UPDATE product_complaint_match SET stale_since = ?, stale_reason = ?,
                        updated_at = ?
                    WHERE product_id = ? AND complaint_id = ? AND stale_since IS NULL
                    RETURNING id
                    """,
                    [moment, motivo, moment, product_id, complaint_id],
                ).fetchone()
                if row is not None:
                    marked += 1
        return marked

    def review(
        self, match_id: int, *, status: MatchStatus, reviewed_by: str, note: str | None
    ) -> ProductComplaintMatch:
        """Registra a decisão humana sem tocar na evidência nem na decisão automática."""
        self.get(match_id)
        now = utc_now()
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE product_complaint_match SET reviewed_status = ?, reviewed_by = ?,
                    reviewed_at = ?, review_note = ?, updated_at = ?
                WHERE id = ?
                """,
                [str(status), reviewed_by, now, note, now, match_id],
            )
        return self.get(match_id)

    @staticmethod
    def _to_match(row: tuple) -> ProductComplaintMatch:
        # A ordem das colunas lidas é sempre `_MATCH_FIELDS`, e o nome de cada coluna é o
        # nome do campo no schema: mapear por nome evita índice errado ao acrescentar coluna.
        return ProductComplaintMatch(**dict(zip(_MATCH_FIELDS, row, strict=True)))
