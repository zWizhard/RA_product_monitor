"""
Persistência de produtos.

Regras que ficam aqui, e não na API:
- duplicidade é decidida pela chave natural (nome + marca + modelo normalizados);
- produto não é removido: é desativado, preservando id, histórico e termos.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb

from app.db import Database
from app.models import Product, ProductCreate, ProductUpdate
from app.normalization import normalize_text
from app.taxonomy import Category, canonical_subcategory, validate_pair

_COLUMNS = (
    "id, name, category, subcategory, brand, manufacturer, model, "
    "regulatory_id, active, natural_key, created_at, updated_at"
)
_TERM_KINDS = {"aliases": "alias", "search_terms": "search"}


class RecordNotFound(LookupError):
    """Registro inexistente. Base comum traduzida para 404 pela API."""


class ProductNotFound(RecordNotFound):
    """Produto inexistente."""


class DuplicateProduct(ValueError):
    """Já existe produto com a mesma chave natural."""

    def __init__(self, natural_key: str, existing_id: int):
        super().__init__(
            f"produto duplicado (chave natural '{natural_key}'); "
            f"já cadastrado com id {existing_id}"
        )
        self.natural_key = natural_key
        self.existing_id = existing_id


def utc_now() -> datetime:
    """Instante atual em UTC, sem tzinfo.

    DuckDB só devolve `TIMESTAMPTZ` para Python com `pytz` instalado; as colunas
    de tempo são `TIMESTAMP` e guardam sempre UTC.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def natural_key(name: str, brand: str | None, model: str | None) -> str:
    """Identidade do produto para efeito de duplicidade, independente de acentos e caixa."""
    return "|".join(normalize_text(part) for part in (name, brand, model))


class ProductRepository:
    def __init__(self, db: Database):
        self._db = db

    # ---------------------------------------------------------------- leitura

    def get(self, product_id: int) -> Product:
        with self._db.reading() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM product WHERE id = ?", [product_id]
            ).fetchone()
            if row is None:
                raise ProductNotFound(f"produto {product_id} não encontrado")
            terms = self._terms(conn, [product_id])
        return self._to_product(row, terms.get(product_id, {}))

    def list(
        self,
        *,
        category: Category | None = None,
        subcategory: str | None = None,
        active: bool | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Product]:
        """Filtro determinístico; `query` casa por substring na forma normalizada."""
        where: list[str] = []
        params: list[object] = []
        if category is not None:
            where.append("category = ?")
            params.append(str(category))
        # A subcategoria é consultada na mesma forma canônica em que foi gravada.
        canonical = canonical_subcategory(subcategory)
        if canonical is not None:
            where.append("subcategory = ?")
            params.append(canonical)
        if active is not None:
            where.append("active = ?")
            params.append(active)
        normalized_query = normalize_text(query)
        if normalized_query:
            where.append(
                "(contains(natural_key, ?) OR EXISTS ("
                " SELECT 1 FROM product_term t"
                " WHERE t.product_id = product.id AND contains(t.normalized, ?)))"
            )
            params.extend([normalized_query, normalized_query])
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._db.reading() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM product{clause} ORDER BY name, id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            terms = self._terms(conn, [row[0] for row in rows])
        return [self._to_product(row, terms.get(row[0], {})) for row in rows]

    # ----------------------------------------------------------------- escrita

    def create(self, data: ProductCreate) -> Product:
        key = natural_key(data.name, data.brand, data.model)
        now = utc_now()
        with self._db.transaction() as conn:
            self._assert_unique(conn, key)
            product_id = conn.execute(
                """
                INSERT INTO product (
                    name, category, subcategory, brand, manufacturer, model,
                    regulatory_id, active, natural_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                [
                    data.name,
                    str(data.category),
                    data.subcategory,
                    data.brand,
                    data.manufacturer,
                    data.model,
                    data.regulatory_id,
                    data.active,
                    key,
                    now,
                    now,
                ],
            ).fetchone()[0]
            self._replace_terms(conn, product_id, "alias", data.aliases, now)
            self._replace_terms(conn, product_id, "search", data.search_terms, now)
        return self.get(product_id)

    def update(self, product_id: int, patch: ProductUpdate) -> Product:
        changes = patch.model_dump(exclude_unset=True)
        current = self.get(product_id)
        if not changes:
            return current

        merged = {field: changes[field] for field in changes}
        category = changes.get("category", current.category)
        if "category" in changes or "subcategory" in changes:
            merged["category"] = category
            merged["subcategory"] = validate_pair(
                category, changes.get("subcategory", current.subcategory)
            )
        key = natural_key(
            changes.get("name", current.name),
            changes.get("brand", current.brand),
            changes.get("model", current.model),
        )
        now = utc_now()

        columns = {field: value for field, value in merged.items() if field not in _TERM_KINDS}
        if "category" in columns:
            columns["category"] = str(columns["category"])
        columns["natural_key"] = key
        columns["updated_at"] = now

        with self._db.transaction() as conn:
            if key != current.natural_key:
                self._assert_unique(conn, key, ignore_id=product_id)
            assignments = ", ".join(f"{field} = ?" for field in columns)
            conn.execute(
                f"UPDATE product SET {assignments} WHERE id = ?",
                [*columns.values(), product_id],
            )
            for field, kind in _TERM_KINDS.items():
                if field in changes:
                    self._replace_terms(conn, product_id, kind, merged[field], now)
        return self.get(product_id)

    def set_active(self, product_id: int, active: bool) -> Product:
        """Desativa/reativa sem apagar dados — a rastreabilidade do cadastro é preservada."""
        self.get(product_id)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE product SET active = ?, updated_at = ? WHERE id = ?",
                [active, utc_now(), product_id],
            )
        return self.get(product_id)

    # -------------------------------------------------------------- auxiliares

    def _assert_unique(
        self,
        conn: duckdb.DuckDBPyConnection,
        key: str,
        ignore_id: int | None = None,
    ) -> None:
        row = conn.execute(
            "SELECT id FROM product WHERE natural_key = ? AND id IS DISTINCT FROM ?",
            [key, ignore_id],
        ).fetchone()
        if row is not None:
            raise DuplicateProduct(key, row[0])

    def _replace_terms(
        self,
        conn: duckdb.DuckDBPyConnection,
        product_id: int,
        kind: str,
        terms: list[str],
        now: datetime,
    ) -> None:
        conn.execute(
            "DELETE FROM product_term WHERE product_id = ? AND kind = ?",
            [product_id, kind],
        )
        for term in terms:
            conn.execute(
                """
                INSERT INTO product_term (product_id, kind, term, normalized, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [product_id, kind, term, normalize_text(term), now],
            )

    @staticmethod
    def _terms(
        conn: duckdb.DuckDBPyConnection, product_ids: list[int]
    ) -> dict[int, dict[str, list[str]]]:
        if not product_ids:
            return {}
        placeholders = ", ".join("?" for _ in product_ids)
        rows = conn.execute(
            f"""
            SELECT product_id, kind, term FROM product_term
            WHERE product_id IN ({placeholders})
            ORDER BY product_id, kind, id
            """,
            product_ids,
        ).fetchall()
        grouped: dict[int, dict[str, list[str]]] = {}
        for product_id, kind, term in rows:
            grouped.setdefault(product_id, {}).setdefault(kind, []).append(term)
        return grouped

    @staticmethod
    def _to_product(row: tuple, terms: dict[str, list[str]]) -> Product:
        return Product(
            id=row[0],
            name=row[1],
            category=Category(row[2]),
            subcategory=row[3],
            brand=row[4],
            manufacturer=row[5],
            model=row[6],
            regulatory_id=row[7],
            active=row[8],
            natural_key=row[9],
            created_at=row[10],
            updated_at=row[11],
            aliases=terms.get("alias", []),
            search_terms=terms.get("search", []),
        )
