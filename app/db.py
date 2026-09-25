"""
Acesso ao DuckDB.

O schema evolui por migrações ordenadas e idempotentes, registradas em
`schema_meta`. Nada é reescrito ou apagado por inicialização.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

# Cada entrada é aplicada uma única vez, em ordem, e grava a versão em `schema_meta`.
MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            "CREATE SEQUENCE IF NOT EXISTS product_id_seq START 1",
            """
            CREATE TABLE IF NOT EXISTS product (
                id            BIGINT PRIMARY KEY DEFAULT nextval('product_id_seq'),
                name          VARCHAR NOT NULL,
                category      VARCHAR NOT NULL,
                subcategory   VARCHAR,
                brand         VARCHAR,
                manufacturer  VARCHAR,
                model         VARCHAR,
                regulatory_id VARCHAR,
                active        BOOLEAN NOT NULL DEFAULT TRUE,
                natural_key   VARCHAR NOT NULL UNIQUE,
                created_at    TIMESTAMP NOT NULL,   -- UTC
                updated_at    TIMESTAMP NOT NULL    -- UTC
            )
            """,
            "CREATE SEQUENCE IF NOT EXISTS product_term_id_seq START 1",
            # Sem FOREIGN KEY: no DuckDB, atualizar coluna indexada do pai (aqui
            # `natural_key`) equivale a delete+insert e quebra a restrição. A integridade
            # de `product_id` é garantida no repositório, que nunca apaga produtos.
            """
            CREATE TABLE IF NOT EXISTS product_term (
                id         BIGINT PRIMARY KEY DEFAULT nextval('product_term_id_seq'),
                product_id BIGINT NOT NULL,
                kind       VARCHAR NOT NULL CHECK (kind IN ('alias', 'search')),
                term       VARCHAR NOT NULL,
                normalized VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,   -- UTC
                UNIQUE (product_id, kind, normalized)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_product_categoria ON product (category, subcategory)",
            "CREATE INDEX IF NOT EXISTS idx_product_term_produto ON product_term (product_id)",
            "CREATE INDEX IF NOT EXISTS idx_product_term_normalized ON product_term (normalized)",
        ),
    ),
    (
        2,
        (
            # Execução de busca: um termo consultado em uma fonte, com as métricas do que
            # aquela execução produziu. É o registro que torna a coleta auditável.
            "CREATE SEQUENCE IF NOT EXISTS search_run_id_seq START 1",
            """
            CREATE TABLE IF NOT EXISTS search_run (
                id              BIGINT PRIMARY KEY DEFAULT nextval('search_run_id_seq'),
                product_id      BIGINT,             -- nulo quando a busca é avulsa
                term            VARCHAR NOT NULL,
                term_normalized VARCHAR NOT NULL,
                source          VARCHAR NOT NULL,
                status          VARCHAR NOT NULL
                                CHECK (status IN ('running', 'completed', 'failed')),
                pages_requested INTEGER NOT NULL,
                found           INTEGER NOT NULL DEFAULT 0,  -- itens detectados
                collected       INTEGER NOT NULL DEFAULT 0,  -- itens válidos, com URL real
                inserted        INTEGER NOT NULL DEFAULT 0,  -- novos em `complaint`
                duplicates      INTEGER NOT NULL DEFAULT 0,  -- já conhecidos
                failures        INTEGER NOT NULL DEFAULT 0,  -- descartes e erros de página
                notes           VARCHAR NOT NULL DEFAULT '[]',  -- JSON: o que falhou e por quê
                started_at      TIMESTAMP NOT NULL,  -- UTC
                finished_at     TIMESTAMP            -- UTC
            )
            """,
            # Reclamação como veio da fonte. Conteúdo original não é sobrescrito por
            # coletas posteriores; só `last_seen_at` avança.
            "CREATE SEQUENCE IF NOT EXISTS complaint_id_seq START 1",
            """
            CREATE TABLE IF NOT EXISTS complaint (
                id             BIGINT PRIMARY KEY DEFAULT nextval('complaint_id_seq'),
                source         VARCHAR NOT NULL,
                external_id    VARCHAR,          -- id da reclamação na fonte
                url            VARCHAR NOT NULL, -- URL canônica; nunca sintetizada
                dedupe_key     VARCHAR NOT NULL UNIQUE,
                title          VARCHAR NOT NULL,
                description    VARCHAR,
                company        VARCHAR,
                location       VARCHAR,
                status         VARCHAR,          -- status exibido pelo Reclame Aqui
                published_at   TIMESTAMP,        -- horário local da fonte, quando legível
                published_text VARCHAR,          -- texto de data como publicado
                raw            VARCHAR NOT NULL, -- JSON bruto extraído, para rastreabilidade
                collected_at   TIMESTAMP NOT NULL,  -- UTC, primeira coleta
                last_seen_at   TIMESTAMP NOT NULL   -- UTC, última vez vista em uma busca
            )
            """,
            # Qual execução de busca encontrou qual reclamação, e em que posição.
            "CREATE SEQUENCE IF NOT EXISTS search_hit_id_seq START 1",
            """
            CREATE TABLE IF NOT EXISTS search_hit (
                id            BIGINT PRIMARY KEY DEFAULT nextval('search_hit_id_seq'),
                search_run_id BIGINT NOT NULL,
                complaint_id  BIGINT NOT NULL,
                page          INTEGER,
                position      INTEGER,
                first_seen    BOOLEAN NOT NULL,  -- esta execução inseriu a reclamação
                created_at    TIMESTAMP NOT NULL,  -- UTC
                UNIQUE (search_run_id, complaint_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_complaint_fonte ON complaint (source, external_id)",
            "CREATE INDEX IF NOT EXISTS idx_search_run_produto ON search_run (product_id)",
            "CREATE INDEX IF NOT EXISTS idx_search_hit_complaint ON search_hit (complaint_id)",
        ),
    ),
    (
        3,
        (
            # Relação central do sistema: por que este produto foi associado a esta
            # reclamação. Guarda o método, o score, o termo, a evidência textual e a
            # configuração que decidiu — e separa decisão automática de decisão humana.
            "CREATE SEQUENCE IF NOT EXISTS product_complaint_match_id_seq START 1",
            """
            CREATE TABLE IF NOT EXISTS product_complaint_match (
                id              BIGINT PRIMARY KEY
                                DEFAULT nextval('product_complaint_match_id_seq'),
                product_id      BIGINT NOT NULL,
                complaint_id    BIGINT NOT NULL,
                method          VARCHAR NOT NULL,   -- camada que produziu a evidência
                score           DOUBLE NOT NULL,
                matched_term    VARCHAR NOT NULL,   -- o que casou, como cadastrado
                evidence_field  VARCHAR NOT NULL,   -- title, description ou company
                evidence        VARCHAR NOT NULL,   -- trecho original da reclamação
                explanation     VARCHAR NOT NULL,   -- resposta ao "por quê", em texto
                status          VARCHAR NOT NULL    -- decisão automática
                                CHECK (status IN ('confirmed', 'possible', 'discarded')),
                matcher_version VARCHAR NOT NULL,   -- configuração de limiares usada
                reviewed_status VARCHAR             -- decisão humana; nunca sobrescrita
                                CHECK (reviewed_status IS NULL OR reviewed_status IN
                                       ('confirmed', 'possible', 'discarded')),
                reviewed_by     VARCHAR,
                reviewed_at     TIMESTAMP,          -- UTC
                review_note     VARCHAR,
                created_at      TIMESTAMP NOT NULL, -- UTC
                updated_at      TIMESTAMP NOT NULL, -- UTC
                UNIQUE (product_id, complaint_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_match_produto"
            " ON product_complaint_match (product_id)",
            "CREATE INDEX IF NOT EXISTS idx_match_complaint"
            " ON product_complaint_match (complaint_id)",
            "CREATE INDEX IF NOT EXISTS idx_match_status ON product_complaint_match (status)",
        ),
    ),
    (
        4,
        (
            # Match cujo par deixou de ter evidência (termo removido do cadastro, produto
            # renomeado) não é apagado: a evidência que levou à associação é preservada e
            # a linha passa a ser datada como obsoleta.
            # UTC; nulo = evidência ainda vigente.
            "ALTER TABLE product_complaint_match ADD COLUMN IF NOT EXISTS"
            " stale_since TIMESTAMP",
        ),
    ),
    (
        5,
        (
            # Arbitragem por especificidade: o produto mais específico que cobre o mesmo
            # trecho. Nulo = nenhum. A linha continua sendo candidata, com a evidência
            # intacta; o que ela perde é a confirmação automática.
            "ALTER TABLE product_complaint_match ADD COLUMN IF NOT EXISTS"
            " shadowed_by_product_id BIGINT",
            # Proveniência da decisão automática: antes de um reprocessamento reescrever
            # método, score ou evidência de um par, a decisão anterior — e a versão do
            # motor que a produziu — fica registrada aqui. É o que torna uma troca de
            # `matcher_version` auditável em vez de destrutiva.
            "CREATE SEQUENCE IF NOT EXISTS product_complaint_match_revision_id_seq START 1",
            """
            CREATE TABLE IF NOT EXISTS product_complaint_match_revision (
                id              BIGINT PRIMARY KEY
                                DEFAULT nextval('product_complaint_match_revision_id_seq'),
                match_id        BIGINT NOT NULL,
                product_id      BIGINT NOT NULL,
                complaint_id    BIGINT NOT NULL,
                method          VARCHAR NOT NULL,
                score           DOUBLE NOT NULL,
                matched_term    VARCHAR NOT NULL,
                evidence_field  VARCHAR NOT NULL,
                evidence        VARCHAR NOT NULL,   -- trecho original da reclamação
                explanation     VARCHAR NOT NULL,
                status          VARCHAR NOT NULL,   -- decisão automática de então
                matcher_version VARCHAR NOT NULL,   -- versão que decidiu
                shadowed_by_product_id BIGINT,
                stale_since     TIMESTAMP,          -- UTC
                decided_at      TIMESTAMP NOT NULL, -- UTC, quando aquela decisão foi gravada
                superseded_at   TIMESTAMP NOT NULL  -- UTC, quando foi substituída
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_match_revision_match"
            " ON product_complaint_match_revision (match_id)",
        ),
    ),
    (
        6,
        (
            # Por que o par deixou de ter evidência nesta execução: perdeu o termo que o
            # sustentava, ou foi suprimido por uma regra de arbitragem — e qual delas.
            # Sem isto, uma linha datada como obsoleta não diz se o motor deixou de achar
            # ou se decidiu que ela não devia existir. A decisão anterior continua na
            # própria linha, com a `matcher_version` que a produziu.
            "ALTER TABLE product_complaint_match ADD COLUMN IF NOT EXISTS"
            " stale_reason VARCHAR",
        ),
    ),
    (
        7,
        (
            # O histórico copia o motivo junto com a decisão que ele acompanhava.
            #
            # Isto pertenceria à migração 6, mas ela já tinha sido aplicada ao banco real
            # quando a necessidade apareceu — e migração aplicada não se edita: a versão
            # fica registrada em `schema_meta` e os comandos acrescentados depois nunca
            # rodam, deixando o schema parcialmente migrado. Uma migração nova é a única
            # forma de alcançar um banco que já passou pela anterior.
            "ALTER TABLE product_complaint_match_revision ADD COLUMN IF NOT EXISTS"
            " stale_reason VARCHAR",
        ),
    ),
)

SCHEMA_VERSION = str(MIGRATIONS[-1][0])


class Database:
    """Conexão única ao DuckDB, serializada por lock (FastAPI executa rotas em threadpool)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.RLock()

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("Banco não conectado. Chame connect() antes.")
        return self._conn

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.path))
        self._migrate()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def reading(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Acesso somente leitura, serializado."""
        with self._lock:
            yield self.conn

    @contextmanager
    def transaction(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Escrita atômica: erro desfaz tudo que a operação tinha feito."""
        with self._lock:
            self.conn.execute("BEGIN TRANSACTION")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self._lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key        VARCHAR PRIMARY KEY,
                    value      VARCHAR NOT NULL,
                    updated_at TIMESTAMP DEFAULT now()
                )
                """
            )
            current = int(self.schema_version() or 0)
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                for statement in statements:
                    self.conn.execute(statement)
                self._record_version(version)

    def _record_version(self, version: int) -> None:
        self.conn.execute(
            """
            INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()
            """,
            [str(version)],
        )

    def schema_version(self) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        return row[0] if row else None

    def healthy(self) -> bool:
        try:
            with self.reading() as conn:
                return conn.execute("SELECT 1").fetchone()[0] == 1
        except Exception:
            return False
