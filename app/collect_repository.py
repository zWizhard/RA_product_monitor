"""
Persistência de buscas e reclamações.

Regras que ficam aqui, e não na API:
- a identidade da reclamação é o id da fonte, ou a URL canônica quando ele não existe;
- reclamação já conhecida não tem o conteúdo sobrescrito por uma coleta posterior;
  apenas `last_seen_at` avança, para que a evidência original continue intacta;
- toda reclamação guarda por qual execução de busca e termo foi encontrada.
"""

from __future__ import annotations

import json
from datetime import datetime

from app.collector import dedupe_key
from app.db import Database
from app.models import Complaint, ComplaintCreate, ComplaintDetail, SearchHit, SearchRun
from app.normalization import normalize_text
from app.repository import RecordNotFound, utc_now

_COMPLAINT_COLUMNS = (
    "id, source, external_id, url, dedupe_key, title, description, company, "
    "location, status, published_at, published_text, collected_at, last_seen_at"
)
_RUN_COLUMNS = (
    "id, product_id, term, term_normalized, source, status, pages_requested, "
    "found, collected, inserted, duplicates, failures, notes, started_at, finished_at"
)
_MAX_NOTES = 50


class ComplaintNotFound(RecordNotFound):
    """Reclamação inexistente."""


class SearchRunNotFound(RecordNotFound):
    """Execução de busca inexistente."""


class ComplaintRepository:
    def __init__(self, db: Database):
        self._db = db

    # ---------------------------------------------------------------- leitura

    def get(self, complaint_id: int) -> Complaint:
        with self._db.reading() as conn:
            row = conn.execute(
                f"SELECT {_COMPLAINT_COLUMNS} FROM complaint WHERE id = ?", [complaint_id]
            ).fetchone()
        if row is None:
            raise ComplaintNotFound(f"reclamação {complaint_id} não encontrada")
        return self._to_complaint(row)

    def detail(self, complaint_id: int) -> ComplaintDetail:
        """Reclamação mais a procedência: cada busca/termo que a encontrou."""
        complaint = self.get(complaint_id)
        with self._db.reading() as conn:
            rows = conn.execute(
                """
                SELECT h.search_run_id, r.term, r.product_id, h.page, h.position,
                       h.first_seen, h.created_at
                FROM search_hit h JOIN search_run r ON r.id = h.search_run_id
                WHERE h.complaint_id = ?
                ORDER BY h.created_at, h.id
                """,
                [complaint_id],
            ).fetchall()
        hits = [
            SearchHit(
                search_run_id=row[0],
                term=row[1],
                product_id=row[2],
                page=row[3],
                position=row[4],
                first_seen=row[5],
                created_at=row[6],
            )
            for row in rows
        ]
        return ComplaintDetail(**complaint.model_dump(), found_by=hits)

    def list(
        self,
        *,
        source: str | None = None,
        search_run_id: int | None = None,
        product_id: int | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Complaint]:
        where: list[str] = []
        params: list[object] = []
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if search_run_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM search_hit h"
                " WHERE h.complaint_id = complaint.id AND h.search_run_id = ?)"
            )
            params.append(search_run_id)
        if product_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM search_hit h JOIN search_run r ON r.id = h.search_run_id"
                " WHERE h.complaint_id = complaint.id AND r.product_id = ?)"
            )
            params.append(product_id)
        # Busca textual determinística sobre título e descrição, na forma normalizada.
        normalized_query = normalize_text(query)
        if normalized_query:
            where.append(
                "contains(regexp_replace(strip_accents(lower(title || ' ' ||"
                " coalesce(description, ''))), '[^0-9a-z]+', ' ', 'g'), ?)"
            )
            params.append(normalized_query)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._db.reading() as conn:
            rows = conn.execute(
                f"SELECT {_COMPLAINT_COLUMNS} FROM complaint{clause}"
                " ORDER BY collected_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._to_complaint(row) for row in rows]

    # ----------------------------------------------------------------- escrita

    def upsert(self, data: ComplaintCreate, now: datetime | None = None) -> tuple[int, bool]:
        """Grava a reclamação se for nova. Devolve `(id, inserida_agora)`.

        Reclamação já conhecida não é reescrita: apenas registramos que ela voltou a
        aparecer. O conteúdo coletado da primeira vez é a evidência preservada.
        """
        moment = now or utc_now()
        key = dedupe_key(data.source, data.url, data.external_id)
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM complaint WHERE dedupe_key = ?", [key]
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE complaint SET last_seen_at = ? WHERE id = ?", [moment, row[0]]
                )
                return row[0], False
            complaint_id = conn.execute(
                """
                INSERT INTO complaint (
                    source, external_id, url, dedupe_key, title, description, company,
                    location, status, published_at, published_text, raw,
                    collected_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                [
                    data.source,
                    data.external_id,
                    data.url,
                    key,
                    data.title,
                    data.description,
                    data.company,
                    data.location,
                    data.status,
                    data.published_at,
                    data.published_text,
                    json.dumps(data.raw, ensure_ascii=False, default=str),
                    moment,
                    moment,
                ],
            ).fetchone()[0]
        return complaint_id, True

    @staticmethod
    def _to_complaint(row: tuple) -> Complaint:
        return Complaint(
            id=row[0],
            source=row[1],
            external_id=row[2],
            url=row[3],
            dedupe_key=row[4],
            title=row[5],
            description=row[6],
            company=row[7],
            location=row[8],
            status=row[9],
            published_at=row[10],
            published_text=row[11],
            collected_at=row[12],
            last_seen_at=row[13],
        )


class SearchRunRepository:
    def __init__(self, db: Database):
        self._db = db

    # ---------------------------------------------------------------- leitura

    def get(self, run_id: int) -> SearchRun:
        with self._db.reading() as conn:
            row = conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM search_run WHERE id = ?", [run_id]
            ).fetchone()
        if row is None:
            raise SearchRunNotFound(f"busca {run_id} não encontrada")
        return self._to_run(row)

    def list(
        self,
        *,
        product_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SearchRun]:
        where: list[str] = []
        params: list[object] = []
        if product_id is not None:
            where.append("product_id = ?")
            params.append(product_id)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._db.reading() as conn:
            rows = conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM search_run{clause}"
                " ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._to_run(row) for row in rows]

    # ----------------------------------------------------------------- escrita

    def start(self, *, term: str, source: str, pages: int, product_id: int | None) -> int:
        """Abre a execução antes de coletar: uma busca que falhar também fica registrada."""
        with self._db.transaction() as conn:
            return conn.execute(
                """
                INSERT INTO search_run (
                    product_id, term, term_normalized, source, status,
                    pages_requested, started_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                RETURNING id
                """,
                [product_id, term, normalize_text(term), source, pages, utc_now()],
            ).fetchone()[0]

    def finish(
        self,
        run_id: int,
        *,
        status: str,
        found: int = 0,
        collected: int = 0,
        inserted: int = 0,
        duplicates: int = 0,
        failures: int = 0,
        notes: list[str] | None = None,
    ) -> SearchRun:
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE search_run SET status = ?, found = ?, collected = ?, inserted = ?,
                    duplicates = ?, failures = ?, notes = ?, finished_at = ?
                WHERE id = ?
                """,
                [
                    status,
                    found,
                    collected,
                    inserted,
                    duplicates,
                    failures,
                    json.dumps((notes or [])[:_MAX_NOTES], ensure_ascii=False),
                    utc_now(),
                    run_id,
                ],
            )
        return self.get(run_id)

    def record_hit(
        self,
        *,
        run_id: int,
        complaint_id: int,
        page: int | None,
        position: int | None,
        first_seen: bool,
    ) -> None:
        """Registra que esta execução encontrou esta reclamação; repetição não duplica linha."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO search_hit (
                    search_run_id, complaint_id, page, position, first_seen, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (search_run_id, complaint_id) DO NOTHING
                """,
                [run_id, complaint_id, page, position, first_seen, utc_now()],
            )

    @staticmethod
    def _to_run(row: tuple) -> SearchRun:
        return SearchRun(
            id=row[0],
            product_id=row[1],
            term=row[2],
            term_normalized=row[3],
            source=row[4],
            status=row[5],
            pages_requested=row[6],
            found=row[7],
            collected=row[8],
            inserted=row[9],
            duplicates=row[10],
            failures=row[11],
            notes=json.loads(row[12] or "[]"),
            started_at=row[13],
            finished_at=row[14],
        )
