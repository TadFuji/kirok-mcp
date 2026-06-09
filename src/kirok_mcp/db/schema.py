"""Schema creation and migrations for MemoryDB.

Tables, FTS5 (trigram) indexes, and the sqlite-vec virtual tables,
including the self-healing reconciliation of the vec indexes.
"""

import logging
import sqlite3

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - optional accelerator, brute-force fallback
    sqlite_vec = None

from kirok_mcp.db.base import _join_json_list
from kirok_mcp.embeddings import EMBEDDING_DIM


logger = logging.getLogger("kirok.db")


class SchemaMixin:
    """Schema and migration methods (expects self.conn, self._vec_available)."""

    def _load_vec_extension(self) -> None:
        """Load the sqlite-vec extension if available.

        On success sets ``self._vec_available = True`` so the schema setup can
        create the ``vec_memories`` virtual table. On any failure (extension not
        installed, Python built without ``enable_load_extension``, etc.) we log a
        warning and leave ``_vec_available`` False — searches then use the
        existing brute-force cosine path.
        """
        assert self.conn is not None

        if sqlite_vec is None:
            logger.warning(
                "sqlite-vec unavailable, falling back to brute-force search.\n"
                "Install sqlite-vec for improved performance at scale."
            )
            self._vec_available = False
            return

        try:
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            self._vec_available = True
        except Exception as e:
            logger.warning(
                "sqlite-vec unavailable, falling back to brute-force search.\n"
                "Install sqlite-vec for improved performance at scale.\n"
                "Reason: %s",
                e,
            )
            self._vec_available = False

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        assert self.conn is not None

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                bank_id TEXT NOT NULL,
                content TEXT NOT NULL,
                entities TEXT DEFAULT '[]',
                keywords TEXT DEFAULT '[]',
                context TEXT DEFAULT '',
                embedding BLOB,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_memories_bank
                ON memories(bank_id);

            CREATE INDEX IF NOT EXISTS idx_memories_timestamp
                ON memories(bank_id, timestamp);

            CREATE TABLE IF NOT EXISTS mental_models (
                id TEXT PRIMARY KEY,
                bank_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                insight TEXT NOT NULL,
                based_on TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_models_bank
                ON mental_models(bank_id);

            CREATE TABLE IF NOT EXISTS bank_config (
                bank_id TEXT PRIMARY KEY,
                retain_mission TEXT DEFAULT '',
                observations_mission TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                bank_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source_memory_ids TEXT DEFAULT '[]',
                embedding BLOB,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_observations_bank
                ON observations(bank_id);

            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_id TEXT NOT NULL DEFAULT '',
                event TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_system_events_bank
                ON system_events(bank_id, created_at);
        """)

        # FTS5 virtual tables (created separately — cannot use IF NOT EXISTS
        # inside executescript for virtual tables on all SQLite versions)
        for ddl in [
            """CREATE VIRTUAL TABLE fts_memories USING fts5(
                id UNINDEXED, bank_id UNINDEXED,
                content, entities, keywords, context,
                tokenize='trigram'
            )""",
            """CREATE VIRTUAL TABLE fts_observations USING fts5(
                id UNINDEXED, bank_id UNINDEXED, content,
                tokenize='trigram'
            )""",
        ]:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # Already exists

        # Schema migrations for existing databases
        self._migrate_schema()

        # sqlite-vec virtual table for fast KNN search (only when the extension
        # loaded). The width is derived from EMBEDDING_DIM so the schema can
        # never drift from the stored vectors.
        if self._vec_available:
            self._init_vec_schema()

        self.conn.commit()

        # Rebuild FTS indexes with the trigram tokenizer if they predate it (the
        # CREATE above matches by name only, so an old unicode61 table survives).
        # Runs post-commit in its own transaction, like the vec backfill — the
        # FTS index is rebuildable from the source tables, so redoing it is safe.
        self._migrate_fts_schema()

        # Backfill the vec tables from existing memories/observations. Done after
        # commit so the base schema is durable before the (potentially large)
        # migration runs.
        if self._vec_available:
            self._migrate_vectors_to_vec_table()
            self._migrate_observation_vectors()

    def _fts_is_trigram(self, fts_name: str) -> bool:
        """Return True if the stored FTS table is declared with the trigram tokenizer."""
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (fts_name,),
        ).fetchone()
        return row is not None and "trigram" in row["sql"].lower()

    def _migrate_fts_schema(self) -> None:
        """Rebuild FTS tables with the trigram tokenizer if they predate it.

        The default ``unicode61`` tokenizer splits CJK into single-character
        tokens, so BM25 barely works for Japanese. ``trigram`` indexes 3-char
        windows and restores Japanese substring matching. ``CREATE VIRTUAL TABLE``
        matches by name only, so an existing unicode61 table is kept silently;
        we detect the tokenizer from the stored SQL and rebuild if needed.

        Each table's DROP → CREATE → backfill runs in a single transaction, so a
        backfill error rolls the DROP back and leaves the old index intact.
        Idempotent: a no-op once both FTS tables are trigram.
        """
        assert self.conn is not None

        # An explicit BEGIN wraps the DROP + CREATE + backfill in one transaction.
        # Without it, Python's sqlite3 auto-commits DDL (DROP/CREATE), so a
        # backfill failure would leave an empty trigram table that the tokenizer
        # check then treats as "already migrated" — silently breaking search. With
        # the explicit transaction, a failure rolls back to the old index and the
        # next connect retries.

        # fts_memories: entities/keywords are stored as JSON arrays but indexed as
        # space-joined text (matching insert_memory), so backfill row-by-row in
        # Python. Malformed JSON on any one row falls back to empty text rather
        # than aborting the whole rebuild.
        if not self._fts_is_trigram("fts_memories"):
            logger.warning("Rebuilding fts_memories with the trigram tokenizer")
            rows = self.conn.execute(
                "SELECT id, bank_id, content, entities, keywords, context "
                "FROM memories"
            ).fetchall()
            try:
                self.conn.execute("BEGIN")
                self.conn.execute("DROP TABLE IF EXISTS fts_memories")
                self.conn.execute(
                    "CREATE VIRTUAL TABLE fts_memories USING fts5("
                    "id UNINDEXED, bank_id UNINDEXED, "
                    "content, entities, keywords, context, "
                    "tokenize='trigram')"
                )
                self.conn.executemany(
                    "INSERT INTO fts_memories "
                    "(id, bank_id, content, entities, keywords, context) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            r["id"],
                            r["bank_id"],
                            r["content"],
                            _join_json_list(r["entities"]),
                            _join_json_list(r["keywords"]),
                            r["context"],
                        )
                        for r in rows
                    ],
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

        # fts_observations: a single content column, so a plain INSERT...SELECT
        # reproduces the index exactly.
        if not self._fts_is_trigram("fts_observations"):
            logger.warning("Rebuilding fts_observations with the trigram tokenizer")
            try:
                self.conn.execute("BEGIN")
                self.conn.execute("DROP TABLE IF EXISTS fts_observations")
                self.conn.execute(
                    "CREATE VIRTUAL TABLE fts_observations USING fts5("
                    "id UNINDEXED, bank_id UNINDEXED, content, "
                    "tokenize='trigram')"
                )
                self.conn.execute(
                    "INSERT INTO fts_observations (id, bank_id, content) "
                    "SELECT id, bank_id, content FROM observations"
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _init_vec_schema(self) -> None:
        """Create (or repair) the vec_memories virtual table.

        The table carries ``bank_id`` as a vec0 *partition key* so KNN search is
        scoped per-bank in SQL (``WHERE bank_id = ? AND embedding MATCH ?``). This
        is essential: vec0 otherwise ranks globally across all banks, and a large
        bank would crowd a smaller bank out of any over-fetch window — breaking
        recall and dedup for the small bank. Cosine distance matches the
        brute-force cosine path so the dedup threshold (0.85) stays meaningful.

        ``CREATE VIRTUAL TABLE IF NOT EXISTS`` matches by name only, so a stale
        table with the wrong width (e.g. a reverted ``float[768]`` attempt) or
        without the partition key would be silently kept. We therefore detect
        either mismatch and DROP before recreating; the next migration backfills.
        """
        assert self.conn is not None

        existing = self.conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='vec_memories'"
        ).fetchone()
        if existing is not None:
            sql = existing["sql"]
            wrong_width = f"float[{EMBEDDING_DIM}]" not in sql
            missing_partition = "partition key" not in sql.lower()
            if wrong_width or missing_partition:
                reason = "width != %s" % EMBEDDING_DIM if wrong_width else "no bank_id partition key"
                logger.warning(
                    "Dropping stale vec_memories (%s) before recreate", reason
                )
                self.conn.execute("DROP TABLE vec_memories")

        self.conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                memory_id TEXT PRIMARY KEY,
                bank_id TEXT partition key,
                embedding float[{EMBEDDING_DIM}] distance_metric=cosine
            )
            """
        )

        # vec_observations mirrors vec_memories exactly (same width / partition /
        # metric), so the same stale-table detection and DROP applies.
        existing_obs = self.conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='vec_observations'"
        ).fetchone()
        if existing_obs is not None:
            sql = existing_obs["sql"]
            wrong_width = f"float[{EMBEDDING_DIM}]" not in sql
            missing_partition = "partition key" not in sql.lower()
            if wrong_width or missing_partition:
                reason = "width != %s" % EMBEDDING_DIM if wrong_width else "no bank_id partition key"
                logger.warning(
                    "Dropping stale vec_observations (%s) before recreate", reason
                )
                self.conn.execute("DROP TABLE vec_observations")

        self.conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_observations USING vec0(
                observation_id TEXT PRIMARY KEY,
                bank_id TEXT partition key,
                embedding float[{EMBEDDING_DIM}] distance_metric=cosine
            )
            """
        )

    def _migrate_vectors_to_vec_table(self) -> None:
        """Reconcile vec_memories with memories.embedding.

        Rebuilds the vec table whenever its memory_id set differs from the set of
        memories carrying a correctly-sized embedding — in EITHER direction. This
        self-heals missing rows (inserts made while the extension was unavailable)
        AND orphan rows (deletes made while it was unavailable); a one-directional
        count guard would catch neither. The rebuild is a single-transaction
        DELETE-all + batched re-insert, because vec0 has no INSERT OR REPLACE on
        its TEXT primary key.

        The steady-state (in-sync) check fetches only ids — never the embedding
        BLOBs — so it stays cheap on large databases. Embeddings are loaded only
        when an actual drift is detected and a rebuild is needed.
        """
        assert self.conn is not None
        if not self._vec_available:
            return

        # Only correctly-sized vectors belong in the float[EMBEDDING_DIM] table;
        # off-size vectors (e.g. test fixtures) stay on the brute-force path.
        expected_bytes = EMBEDDING_DIM * 4  # float32 = 4 bytes each
        mem_ids = {
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM memories "
                "WHERE embedding IS NOT NULL AND length(embedding) = ?",
                (expected_bytes,),
            )
        }
        vec_ids = {
            r["memory_id"]
            for r in self.conn.execute("SELECT memory_id FROM vec_memories")
        }
        if mem_ids == vec_ids:
            return

        logger.info(
            "Reconciling vec_memories (%s memories, %s vec rows)",
            len(mem_ids),
            len(vec_ids),
        )

        rows = self.conn.execute(
            "SELECT id, bank_id, embedding FROM memories "
            "WHERE embedding IS NOT NULL AND length(embedding) = ?",
            (expected_bytes,),
        ).fetchall()

        try:
            # Clean rebuild from memories (the source of truth) repairs drift in
            # either direction in one shot.
            self.conn.execute("DELETE FROM vec_memories")
            batch_size = 500
            done = 0
            total = len(rows)
            for start in range(0, total, batch_size):
                chunk = rows[start:start + batch_size]
                self.conn.executemany(
                    "INSERT INTO vec_memories (memory_id, bank_id, embedding) "
                    "VALUES (?, ?, ?)",
                    [(r["id"], r["bank_id"], r["embedding"]) for r in chunk],
                )
                done += len(chunk)
                logger.info("Migrating vectors: %s/%s", done, total)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _migrate_observation_vectors(self) -> None:
        """Reconcile vec_observations with observations.embedding.

        Symmetric to ``_migrate_vectors_to_vec_table``: rebuilds whenever the
        observation_id set differs from the set of observations carrying a
        correctly-sized embedding, healing both missing and orphan rows. The
        steady-state check fetches only ids, so it stays cheap.
        """
        assert self.conn is not None
        if not self._vec_available:
            return

        expected_bytes = EMBEDDING_DIM * 4  # float32 = 4 bytes each
        obs_ids = {
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM observations "
                "WHERE embedding IS NOT NULL AND length(embedding) = ?",
                (expected_bytes,),
            )
        }
        vec_ids = {
            r["observation_id"]
            for r in self.conn.execute("SELECT observation_id FROM vec_observations")
        }
        if obs_ids == vec_ids:
            return

        logger.info(
            "Reconciling vec_observations (%s observations, %s vec rows)",
            len(obs_ids),
            len(vec_ids),
        )

        rows = self.conn.execute(
            "SELECT id, bank_id, embedding FROM observations "
            "WHERE embedding IS NOT NULL AND length(embedding) = ?",
            (expected_bytes,),
        ).fetchall()

        try:
            self.conn.execute("DELETE FROM vec_observations")
            batch_size = 500
            for start in range(0, len(rows), batch_size):
                chunk = rows[start:start + batch_size]
                self.conn.executemany(
                    "INSERT INTO vec_observations "
                    "(observation_id, bank_id, embedding) VALUES (?, ?, ?)",
                    [(r["id"], r["bank_id"], r["embedding"]) for r in chunk],
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _sync_vec_row(
        self,
        table: str,
        id_column: str,
        row_id: str,
        bank_id: str,
        emb_blob: bytes | None,
    ) -> None:
        """Bring one vec index row in line with its base-table embedding.

        vec0 has no INSERT OR REPLACE on a TEXT primary key, so this is a
        delete-then-insert. Only well-formed EMBEDDING_DIM vectors belong in
        the float[EMBEDDING_DIM] table; off-size or NULL embeddings just
        remove the stale row (those memories stay searchable via the
        brute-force path). No-op when the extension is unavailable.

        Runs inside the caller's transaction — no commit here.
        """
        assert self.conn is not None
        if not self._vec_available:
            return

        self.conn.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (row_id,))
        if emb_blob is not None and len(emb_blob) == EMBEDDING_DIM * 4:
            self.conn.execute(
                f"INSERT INTO {table} ({id_column}, bank_id, embedding) "
                "VALUES (?, ?, ?)",
                (row_id, bank_id, emb_blob),
            )

    def _migrate_schema(self) -> None:
        """Apply incremental schema changes to existing tables."""
        assert self.conn is not None

        migrations = [
            ("memories", "consolidated_at", "TEXT"),
            ("mental_models", "auto_refresh", "INTEGER DEFAULT 0"),
            ("mental_models", "source_query", "TEXT DEFAULT ''"),
        ]
        for table, column, col_type in migrations:
            try:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
