"""SQLite database layer for Kirok memory storage.

Manages memories, mental models, observations, bank configs, and
FTS5 full-text search indexes. Vectors are stored as binary blobs
for efficient retrieval.
"""

import json
import logging
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import re

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - optional accelerator, brute-force fallback
    sqlite_vec = None

from kirok_mcp.embeddings import EMBEDDING_DIM, semantic_search


logger = logging.getLogger("kirok.db")

# Upper bound on retained background-failure rows (oldest pruned on insert),
# keeping the system_events table from growing without bound.
MAX_SYSTEM_EVENTS = 200


def _serialize_vector(vector: list[float]) -> bytes:
    """Serialize a float vector to bytes for SQLite BLOB storage."""
    return struct.pack(f"{len(vector)}f", *vector)


def _deserialize_vector(blob: bytes) -> list[float]:
    """Deserialize bytes back to a float vector."""
    n = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{n}f", blob))


# FTS5 special operators that must not appear as bare tokens in MATCH queries
_FTS5_OPERATORS = re.compile(r'\b(AND|OR|NOT|NEAR)\b', re.IGNORECASE)


def _join_json_list(raw: str) -> str:
    """Space-join a JSON array string for FTS indexing, tolerating bad JSON.

    Mirrors how ``insert_memory`` feeds entities/keywords into the FTS index.
    A row whose JSON is malformed (legacy or hand-edited data) falls back to
    empty text so a single bad row cannot abort an index rebuild.
    """
    try:
        parsed = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return ""
    if isinstance(parsed, list):
        return " ".join(str(x) for x in parsed)
    return ""


def _sanitize_fts_query(query: str) -> str | None:
    """Sanitize a query string for safe use with FTS5 MATCH.

    FTS5 interprets hyphens as NOT, bare uppercase words (e.g. ASCII)
    as column names, and has other special syntax that can cause crashes
    with user-supplied queries (especially Japanese text with hyphens
    like dates '2026-03-25').

    Strategy: wrap each token in double quotes to force literal matching.
    Returns None if no valid tokens remain after sanitization.
    """
    if not query or not query.strip():
        return None

    # Remove FTS5 special characters: *, ^, ", NEAR()
    cleaned = query.replace('"', ' ').replace('*', ' ').replace('^', ' ')
    # Replace hyphens with spaces (prevents NOT interpretation)
    cleaned = cleaned.replace('-', ' ')
    # Remove parentheses used in NEAR()
    cleaned = cleaned.replace('(', ' ').replace(')', ' ')
    # Remove FTS5 operators
    cleaned = _FTS5_OPERATORS.sub(' ', cleaned)

    # Split into tokens. The trigram tokenizer indexes 3-character windows, so
    # tokens shorter than 3 chars can never match — drop them and let semantic
    # search cover those (e.g. 2-char Japanese words). If nothing remains, return
    # None so recall falls back to the semantic path (existing behavior).
    tokens = [t for t in cleaned.split() if len(t) >= 3]
    if not tokens:
        return None

    # Double-quote each token for safe literal matching
    quoted = ' '.join(f'"{t}"' for t in tokens)
    return quoted


def _resolve_db_path(db_path: str | Path | None) -> Path:
    """Resolve the database path.

    Priority:
    1. Explicit db_path argument (if provided)
    2. ~/.kirok/memory.db (default)
    """
    if db_path is not None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    kirok_dir = Path.home() / ".kirok"
    kirok_db = kirok_dir / "memory.db"
    kirok_dir.mkdir(parents=True, exist_ok=True)
    return kirok_db


class MemoryDB:
    """SQLite-backed memory database with FTS5 full-text search."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = _resolve_db_path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        # Set True in connect() once the sqlite-vec extension loads successfully.
        # When False, all vector search transparently falls back to brute force.
        self._vec_available: bool = False

    def connect(self) -> None:
        """Open database connection and initialize schema."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._load_vec_extension()
        self._init_schema()

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

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

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

    # ── Retain ────────────────────────────────────────────────────────

    def insert_memory(
        self,
        bank_id: str,
        content: str,
        embedding: list[float] | None = None,
        entities: list[str] | None = None,
        keywords: list[str] | None = None,
        context: str = "",
        timestamp: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new memory and its FTS index entry. Returns the memory ID."""
        assert self.conn is not None

        memory_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        ts = timestamp or now
        ent = entities or []
        kw = keywords or []
        meta = metadata or {}

        emb_blob = _serialize_vector(embedding) if embedding else None

        try:
            self.conn.execute(
                """INSERT INTO memories
                   (id, bank_id, content, entities, keywords, context,
                    embedding, timestamp, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    bank_id,
                    content,
                    json.dumps(ent),
                    json.dumps(kw),
                    context,
                    emb_blob,
                    ts,
                    now,
                    json.dumps(meta),
                ),
            )

            # Insert into FTS5 index
            self.conn.execute(
                """INSERT INTO fts_memories (id, bank_id, content, entities, keywords, context)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (memory_id, bank_id, content, " ".join(ent), " ".join(kw), context),
            )

            self._sync_vec_row("vec_memories", "memory_id", memory_id, bank_id, emb_blob)

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return memory_id

    # ── Recall: Keyword Search ────────────────────────────────────────

    def fts_search(
        self, bank_id: str, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Full-text search using FTS5 BM25 ranking.

        The query is sanitized to prevent FTS5 parse errors from special
        characters (hyphens interpreted as NOT, uppercase words as column
        names, etc.). If sanitization leaves no valid tokens, or if the
        FTS5 query still fails, returns an empty list gracefully —
        semantic search will still provide results via RRF.
        """
        assert self.conn is not None

        safe_query = _sanitize_fts_query(query)
        if safe_query is None:
            return []

        try:
            rows = self.conn.execute(
                """SELECT fts.id, fts.content, bm25(fts_memories) AS score
                   FROM fts_memories fts
                   WHERE fts_memories MATCH ? AND fts.bank_id = ?
                   ORDER BY score
                   LIMIT ?""",
                (safe_query, bank_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 parse error — fall back to empty (semantic search still works)
            return []

        return [{"id": r["id"], "content": r["content"], "score": r["score"]} for r in rows]

    # ── Recall: Vector Search ─────────────────────────────────────────

    def get_all_embeddings(self, bank_id: str) -> list[dict[str, Any]]:
        """Load all embeddings for a bank (for brute-force cosine similarity)."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT id, content, embedding, timestamp, context, entities, keywords
               FROM memories
               WHERE bank_id = ? AND embedding IS NOT NULL""",
            (bank_id,),
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "content": r["content"],
                "embedding": _deserialize_vector(r["embedding"]),
                "timestamp": r["timestamp"],
                "context": r["context"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
            })
        return results

    def get_embeddings_in_range(
        self,
        bank_id: str,
        time_min: str | None = None,
        time_max: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load embeddings for a bank, optionally restricted to a timestamp range.

        Same row shape as ``get_all_embeddings``, but pushes the time-window
        filter into SQL so time-filtered recall (which must stay on brute force
        because vec0 cannot filter by timestamp) loads only the candidates it
        needs instead of the whole bank. Bounds are inclusive (>= / <=), matching
        the previous in-Python filter and ``search_by_timestamp``.
        """
        assert self.conn is not None

        conditions = ["bank_id = ?", "embedding IS NOT NULL"]
        params: list[Any] = [bank_id]
        if time_min:
            conditions.append("timestamp >= ?")
            params.append(time_min)
        if time_max:
            conditions.append("timestamp <= ?")
            params.append(time_max)
        where = " AND ".join(conditions)

        rows = self.conn.execute(
            f"""SELECT id, content, embedding, timestamp, context, entities, keywords
               FROM memories
               WHERE {where}""",
            params,
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "content": r["content"],
                "embedding": _deserialize_vector(r["embedding"]),
                "timestamp": r["timestamp"],
                "context": r["context"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
            })
        return results

    def _brute_force_search(
        self, query_embedding: list[float], bank_id: str, top_k: int
    ) -> list[dict[str, Any]]:
        """Brute-force cosine search over all embeddings in a bank.

        This is the fallback path for ``vec_search`` and is also called directly
        by tests. Returns dicts identical to ``get_all_embeddings`` entries plus
        a ``similarity`` score.
        """
        candidates = self.get_all_embeddings(bank_id)
        return semantic_search(query_embedding, candidates, top_k=top_k)

    def vec_search(
        self,
        query_embedding: list[float],
        bank_id: str,
        top_k: int,
        *,
        candidate_multiplier: int = 5,
    ) -> list[dict[str, Any]]:
        """Per-bank KNN vector search via sqlite-vec.

        ``bank_id`` is a vec0 partition key, so the KNN itself is scoped to the
        bank (``WHERE bank_id = ? AND embedding MATCH ?``), returning exactly the
        bank's nearest neighbours — true parity with the per-bank brute-force
        path, with no cross-bank crowd-out.

        Falls back to brute-force cosine when: the extension is unavailable, the
        KNN errors, or the vec table returns fewer hits than the bank actually
        holds (a sign vec_memories is out of sync). Each result mirrors
        ``get_all_embeddings`` entries plus a cosine ``similarity``
        (``1 - distance``, clamped >= 0) so RRF and the dedup threshold keep
        working unchanged.

        ``candidate_multiplier`` is retained for API compatibility; per-bank KNN
        needs no over-fetch window, so it no longer affects results.
        """
        assert self.conn is not None

        if not self._vec_available:
            return self._brute_force_search(query_embedding, bank_id, top_k)

        try:
            query_blob = _serialize_vector(query_embedding)
            knn_rows = self.conn.execute(
                """SELECT memory_id, distance
                   FROM vec_memories
                   WHERE bank_id = ? AND embedding MATCH ?
                   ORDER BY distance
                   LIMIT ?""",
                (bank_id, query_blob, max(top_k, 1)),
            ).fetchall()
        except Exception as e:
            logger.warning(
                "vec_search failed, falling back to brute-force search: %s", e
            )
            return self._brute_force_search(query_embedding, bank_id, top_k)

        # Safety net: if the vec table returned fewer hits than the bank actually
        # holds, vec_memories is out of sync (e.g. an out-of-band edit). Fall back
        # to the authoritative brute-force path rather than silently under-return.
        if len(knn_rows) < top_k:
            bank_count = self.conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE bank_id = ? AND embedding IS NOT NULL "
                "AND length(embedding) = ?",
                (bank_id, EMBEDDING_DIM * 4),
            ).fetchone()[0]
            if len(knn_rows) < min(top_k, bank_count):
                logger.warning(
                    "vec_memories under-returned for bank %s (%s of %s); "
                    "falling back to brute-force search",
                    bank_id,
                    len(knn_rows),
                    bank_count,
                )
                return self._brute_force_search(query_embedding, bank_id, top_k)

        if not knn_rows:
            return []

        distances = {r["memory_id"]: r["distance"] for r in knn_rows}
        ids = list(distances.keys())
        placeholders = ",".join("?" for _ in ids)
        # ids come from this bank's vec partition, so the join stays bank-scoped.
        mem_rows = self.conn.execute(
            f"""SELECT id, content, embedding, timestamp,
                       context, entities, keywords
                FROM memories
                WHERE id IN ({placeholders})""",
            ids,
        ).fetchall()

        results = []
        for r in mem_rows:
            similarity = max(0.0, 1.0 - float(distances[r["id"]]))
            results.append({
                "id": r["id"],
                "content": r["content"],
                "embedding": (
                    _deserialize_vector(r["embedding"]) if r["embedding"] else []
                ),
                "timestamp": r["timestamp"],
                "context": r["context"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
                "similarity": similarity,
            })

        # KNN distance order == similarity desc; re-sort after the metadata join.
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    # ── Recall: Get Memory by ID ──────────────────────────────────────

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Get a single memory by ID."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "bank_id": row["bank_id"],
            "content": row["content"],
            "entities": json.loads(row["entities"]),
            "keywords": json.loads(row["keywords"]),
            "context": row["context"],
            "timestamp": row["timestamp"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata"]),
        }

    # ── Mental Models ─────────────────────────────────────────────────

    def insert_mental_model(
        self,
        bank_id: str,
        topic: str,
        insight: str,
        based_on: list[str] | None = None,
    ) -> str:
        """Insert a new mental model (generated by Reflect). Returns the model ID."""
        assert self.conn is not None

        model_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute(
            """INSERT INTO mental_models (id, bank_id, topic, insight, based_on, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (model_id, bank_id, topic, insight, json.dumps(based_on or []), now, now),
        )
        self.conn.commit()
        return model_id

    def get_mental_models(self, bank_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent mental models for a bank."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT * FROM mental_models
               WHERE bank_id = ?
               ORDER BY updated_at DESC
               LIMIT ?""",
            (bank_id, limit),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "topic": r["topic"],
                "insight": r["insight"],
                "based_on": json.loads(r["based_on"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    # ── Stats & Listing ───────────────────────────────────────────────

    def list_banks(self) -> list[dict[str, Any]]:
        """List all memory banks with counts."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT bank_id, COUNT(*) as count,
                      MIN(timestamp) as oldest,
                      MAX(timestamp) as newest
               FROM memories
               GROUP BY bank_id
               ORDER BY newest DESC"""
        ).fetchall()

        return [
            {
                "bank_id": r["bank_id"],
                "memory_count": r["count"],
                "oldest": r["oldest"],
                "newest": r["newest"],
            }
            for r in rows
        ]

    def get_stats(self, bank_id: str) -> dict[str, Any]:
        """Get statistics for a memory bank."""
        assert self.conn is not None

        mem_count = self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE bank_id = ?", (bank_id,)
        ).fetchone()[0]

        model_count = self.conn.execute(
            "SELECT COUNT(*) FROM mental_models WHERE bank_id = ?", (bank_id,)
        ).fetchone()[0]

        obs_count = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE bank_id = ?", (bank_id,)
        ).fetchone()[0]

        return {
            "bank_id": bank_id,
            "memory_count": mem_count,
            "mental_model_count": model_count,
            # COUNT(*) instead of len(get_*(limit=1000)): accurate past 1000 rows
            # and avoids hydrating rows just to count them.
            "observations_count": obs_count,
            "unconsolidated_count": self.count_unconsolidated_memories(bank_id),
        }

    # ── Forget ────────────────────────────────────────────────────────

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory and its FTS index. Returns True if found and deleted."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT id FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return False

        try:
            self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self.conn.execute("DELETE FROM fts_memories WHERE id = ?", (memory_id,))
            if self._vec_available:
                self.conn.execute(
                    "DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,)
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return True

    # ── List Memories (Browsing) ──────────────────────────────────────

    def list_memories(
        self,
        bank_id: str,
        limit: int = 20,
        offset: int = 0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """List memories in a bank with pagination and optional metadata filter."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT id, content, entities, keywords, context,
                      timestamp, created_at, metadata
               FROM memories
               WHERE bank_id = ?
               ORDER BY timestamp DESC
               LIMIT ? OFFSET ?""",
            (bank_id, limit, offset),
        ).fetchall()

        results = []
        for r in rows:
            mem = {
                "id": r["id"],
                "content": r["content"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
                "context": r["context"],
                "timestamp": r["timestamp"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata"]),
            }
            if metadata_filter:
                match = all(
                    mem["metadata"].get(k) == v
                    for k, v in metadata_filter.items()
                )
                if not match:
                    continue
            results.append(mem)
        return results

    # ── Update Memory ─────────────────────────────────────────────────

    def update_memory(
        self,
        memory_id: str,
        content: str | None = None,
        entities: list[str] | None = None,
        keywords: list[str] | None = None,
        context: str | None = None,
        embedding: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Update an existing memory. Returns True if found and updated."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return False

        new_content = content if content is not None else row["content"]
        new_entities = entities if entities is not None else json.loads(row["entities"])
        new_keywords = keywords if keywords is not None else json.loads(row["keywords"])
        new_context = context if context is not None else row["context"]
        new_emb_blob = (
            _serialize_vector(embedding) if embedding is not None else row["embedding"]
        )
        new_metadata = metadata if metadata is not None else json.loads(row["metadata"])

        try:
            self.conn.execute(
                """UPDATE memories
                   SET content = ?, entities = ?, keywords = ?,
                       context = ?, embedding = ?, metadata = ?
                   WHERE id = ?""",
                (
                    new_content,
                    json.dumps(new_entities),
                    json.dumps(new_keywords),
                    new_context,
                    new_emb_blob,
                    json.dumps(new_metadata),
                    memory_id,
                ),
            )

            # Update FTS index
            self.conn.execute("DELETE FROM fts_memories WHERE id = ?", (memory_id,))
            self.conn.execute(
                """INSERT INTO fts_memories (id, bank_id, content, entities, keywords, context)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    row["bank_id"],
                    new_content,
                    " ".join(new_entities),
                    " ".join(new_keywords),
                    new_context,
                ),
            )

            self._sync_vec_row(
                "vec_memories", "memory_id", memory_id, row["bank_id"], new_emb_blob
            )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return True

    def update_memory_embedding(
        self, memory_id: str, embedding: list[float]
    ) -> bool:
        """Replace a memory's embedding (and its vec row) without touching content.

        Used by the re-embedding script: the content/entities/keywords are
        unchanged, only the vector is regenerated. A plain UPDATE of the BLOB
        would leave the vec table holding the stale vector (``_migrate`` only
        rebuilds on an id-set mismatch, not a value change), so the vec row is
        re-synced here via delete-then-insert. Returns False if the id is unknown.

        Raises ValueError on a wrong-size vector — this is for re-embedding
        production data at full fidelity, so a mis-sized vector is a bug we refuse
        to persist (it would silently corrupt the stored embedding width).
        """
        assert self.conn is not None
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"update_memory_embedding expects a {EMBEDDING_DIM}-d vector, "
                f"got {len(embedding)}"
            )

        row = self.conn.execute(
            "SELECT bank_id FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return False

        emb_blob = _serialize_vector(embedding)
        try:
            self.conn.execute(
                "UPDATE memories SET embedding = ? WHERE id = ?",
                (emb_blob, memory_id),
            )
            self._sync_vec_row(
                "vec_memories", "memory_id", memory_id, row["bank_id"], emb_blob
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return True

    # ── Time-Range Search ─────────────────────────────────────────────

    def search_by_timestamp(
        self,
        bank_id: str,
        time_min: str | None = None,
        time_max: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search memories within a time range."""
        assert self.conn is not None

        conditions = ["bank_id = ?"]
        params: list[Any] = [bank_id]

        if time_min:
            conditions.append("timestamp >= ?")
            params.append(time_min)
        if time_max:
            conditions.append("timestamp <= ?")
            params.append(time_max)

        params.append(limit)
        where = " AND ".join(conditions)

        rows = self.conn.execute(
            f"""SELECT id, content, entities, keywords, context,
                       timestamp, created_at, metadata
                FROM memories
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT ?""",
            params,
        ).fetchall()

        return [
            {
                "id": r["id"],
                "content": r["content"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
                "context": r["context"],
                "timestamp": r["timestamp"],
                "created_at": r["created_at"],
                "metadata": json.loads(r["metadata"]),
            }
            for r in rows
        ]

    # ── Bank Management ───────────────────────────────────────────────

    def _delete_bank_data(
        self,
        bank_id: str,
        include_models: bool = False,
        include_config: bool = False,
    ) -> dict[str, int]:
        """Internal helper: delete bank data in a single transaction.

        Always deletes memories, fts_memories, observations, fts_observations.
        Optionally deletes mental_models and bank_config.
        Returns counts for all affected groups.
        """
        assert self.conn is not None

        mem_count = self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE bank_id = ?", (bank_id,)
        ).fetchone()[0]
        obs_count = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE bank_id = ?", (bank_id,)
        ).fetchone()[0]
        model_count = 0
        config_count = 0

        if include_models:
            model_count = self.conn.execute(
                "SELECT COUNT(*) FROM mental_models WHERE bank_id = ?", (bank_id,)
            ).fetchone()[0]
        if include_config:
            config_count = self.conn.execute(
                "SELECT COUNT(*) FROM bank_config WHERE bank_id = ?", (bank_id,)
            ).fetchone()[0]

        has_data = mem_count > 0 or obs_count > 0 or model_count > 0 or config_count > 0
        if not has_data:
            return {
                "memories_deleted": 0,
                "observations_deleted": 0,
                "models_deleted": model_count,
                "config_deleted": config_count,
            }

        try:
            self.conn.execute(
                "DELETE FROM fts_memories WHERE id IN "
                "(SELECT id FROM memories WHERE bank_id = ?)",
                (bank_id,),
            )
            self.conn.execute(
                "DELETE FROM fts_observations WHERE id IN "
                "(SELECT id FROM observations WHERE bank_id = ?)",
                (bank_id,),
            )
            # bank_id is a vec0 partition key, so we can delete this bank's vec
            # rows directly (no dependency on the memories rows still existing).
            if self._vec_available:
                self.conn.execute(
                    "DELETE FROM vec_memories WHERE bank_id = ?", (bank_id,)
                )
                self.conn.execute(
                    "DELETE FROM vec_observations WHERE bank_id = ?", (bank_id,)
                )
            self.conn.execute("DELETE FROM memories WHERE bank_id = ?", (bank_id,))
            self.conn.execute(
                "DELETE FROM observations WHERE bank_id = ?", (bank_id,)
            )
            if include_models:
                self.conn.execute(
                    "DELETE FROM mental_models WHERE bank_id = ?", (bank_id,)
                )
            if include_config:
                self.conn.execute(
                    "DELETE FROM bank_config WHERE bank_id = ?", (bank_id,)
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return {
            "memories_deleted": mem_count,
            "observations_deleted": obs_count,
            "models_deleted": model_count,
            "config_deleted": config_count,
        }

    def clear_bank(self, bank_id: str) -> dict[str, int]:
        """Delete all memories and observations in a bank.

        Mental models and bank configuration are preserved.
        Returns counts for deleted rows.
        """
        result = self._delete_bank_data(bank_id)
        return {
            "memories_deleted": result["memories_deleted"],
            "observations_deleted": result["observations_deleted"],
        }

    def delete_bank(self, bank_id: str) -> dict[str, int]:
        """Delete a bank and all associated memories, observations, models, and config."""
        return self._delete_bank_data(
            bank_id, include_models=True, include_config=True
        )

    # ── Mental Model Management ───────────────────────────────────────

    def get_mental_model(self, model_id: str) -> dict[str, Any] | None:
        """Get a single mental model by ID."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT * FROM mental_models WHERE id = ?", (model_id,)
        ).fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "bank_id": row["bank_id"],
            "topic": row["topic"],
            "insight": row["insight"],
            "based_on": json.loads(row["based_on"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "auto_refresh": bool(row["auto_refresh"]) if row["auto_refresh"] is not None else False,
            "source_query": row["source_query"] or "",
        }

    def delete_mental_model(self, model_id: str) -> bool:
        """Delete a mental model. Returns True if found and deleted."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT id FROM mental_models WHERE id = ?", (model_id,)
        ).fetchone()
        if not row:
            return False

        self.conn.execute("DELETE FROM mental_models WHERE id = ?", (model_id,))
        self.conn.commit()
        return True

    def update_mental_model(
        self,
        model_id: str,
        topic: str | None = None,
        insight: str | None = None,
        based_on: list[str] | None = None,
    ) -> bool:
        """Update an existing mental model. Returns True if found and updated."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT * FROM mental_models WHERE id = ?", (model_id,)
        ).fetchone()
        if not row:
            return False

        new_topic = topic if topic is not None else row["topic"]
        new_insight = insight if insight is not None else row["insight"]
        new_based_on = based_on if based_on is not None else json.loads(row["based_on"])
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute(
            """UPDATE mental_models
               SET topic = ?, insight = ?, based_on = ?, updated_at = ?
               WHERE id = ?""",
            (new_topic, new_insight, json.dumps(new_based_on), now, model_id),
        )
        self.conn.commit()
        return True

    def insert_mental_model_with_options(
        self,
        bank_id: str,
        topic: str,
        insight: str,
        based_on: list[str] | None = None,
        auto_refresh: bool = False,
        source_query: str = "",
    ) -> str:
        """Insert a mental model with auto_refresh and source_query options."""
        assert self.conn is not None

        model_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute(
            """INSERT INTO mental_models
               (id, bank_id, topic, insight, based_on,
                created_at, updated_at, auto_refresh, source_query)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model_id, bank_id, topic, insight,
                json.dumps(based_on or []), now, now,
                1 if auto_refresh else 0, source_query,
            ),
        )
        self.conn.commit()
        return model_id

    def get_auto_refresh_models(self, bank_id: str) -> list[dict[str, Any]]:
        """Get all mental models with auto_refresh enabled for a bank."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT * FROM mental_models
               WHERE bank_id = ? AND auto_refresh = 1""",
            (bank_id,),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "bank_id": r["bank_id"],
                "topic": r["topic"],
                "insight": r["insight"],
                "based_on": json.loads(r["based_on"]),
                "source_query": r["source_query"] or r["topic"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    # ── Bank Config ────────────────────────────────────────────────────

    def get_bank_config(self, bank_id: str) -> dict[str, Any]:
        """Get config for a bank. Returns defaults if not set."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT * FROM bank_config WHERE bank_id = ?", (bank_id,)
        ).fetchone()

        if not row:
            return {
                "bank_id": bank_id,
                "retain_mission": "",
                "observations_mission": "",
            }

        return {
            "bank_id": row["bank_id"],
            "retain_mission": row["retain_mission"] or "",
            "observations_mission": row["observations_mission"] or "",
        }

    def set_bank_config(
        self,
        bank_id: str,
        retain_mission: str | None = None,
        observations_mission: str | None = None,
    ) -> dict[str, Any]:
        """Create or update bank config. Returns the updated config."""
        assert self.conn is not None

        now = datetime.now(timezone.utc).isoformat()
        existing = self.conn.execute(
            "SELECT * FROM bank_config WHERE bank_id = ?", (bank_id,)
        ).fetchone()

        if existing:
            new_rm = retain_mission if retain_mission is not None else existing["retain_mission"]
            new_om = observations_mission if observations_mission is not None else existing["observations_mission"]
            self.conn.execute(
                """UPDATE bank_config
                   SET retain_mission = ?, observations_mission = ?, updated_at = ?
                   WHERE bank_id = ?""",
                (new_rm, new_om, now, bank_id),
            )
        else:
            new_rm = retain_mission or ""
            new_om = observations_mission or ""
            self.conn.execute(
                """INSERT INTO bank_config
                   (bank_id, retain_mission, observations_mission, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (bank_id, new_rm, new_om, now, now),
            )

        self.conn.commit()
        return {
            "bank_id": bank_id,
            "retain_mission": new_rm,
            "observations_mission": new_om,
        }

    # ── Observations ──────────────────────────────────────────────────

    def insert_observation(
        self,
        bank_id: str,
        content: str,
        source_memory_ids: list[str],
        embedding: list[float] | None = None,
    ) -> str:
        """Insert a new observation. Returns the observation ID."""
        assert self.conn is not None

        obs_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        emb_blob = _serialize_vector(embedding) if embedding else None

        try:
            self.conn.execute(
                """INSERT INTO observations
                   (id, bank_id, content, source_memory_ids, embedding,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    obs_id, bank_id, content,
                    json.dumps(source_memory_ids), emb_blob, now, now,
                ),
            )
            self.conn.execute(
                """INSERT INTO fts_observations (id, bank_id, content)
                   VALUES (?, ?, ?)""",
                (obs_id, bank_id, content),
            )

            self._sync_vec_row(
                "vec_observations", "observation_id", obs_id, bank_id, emb_blob
            )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return obs_id

    def update_observation(
        self,
        observation_id: str,
        content: str,
        source_memory_ids: list[str],
        embedding: list[float] | None = None,
    ) -> bool:
        """Update an existing observation. Returns True if found."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT * FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        if not row:
            return False

        now = datetime.now(timezone.utc).isoformat()
        emb_blob = _serialize_vector(embedding) if embedding else row["embedding"]

        try:
            self.conn.execute(
                """UPDATE observations
                   SET content = ?, source_memory_ids = ?,
                       embedding = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    content, json.dumps(source_memory_ids),
                    emb_blob, now, observation_id,
                ),
            )
            self.conn.execute(
                "DELETE FROM fts_observations WHERE id = ?", (observation_id,)
            )
            self.conn.execute(
                """INSERT INTO fts_observations (id, bank_id, content)
                   VALUES (?, ?, ?)""",
                (observation_id, row["bank_id"], content),
            )

            self._sync_vec_row(
                "vec_observations",
                "observation_id",
                observation_id,
                row["bank_id"],
                emb_blob,
            )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return True

    def delete_observation(self, observation_id: str) -> bool:
        """Delete a single observation and its FTS index. Returns True if found."""
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT id FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        if not row:
            return False

        try:
            self.conn.execute(
                "DELETE FROM fts_observations WHERE id = ?", (observation_id,)
            )
            self.conn.execute(
                "DELETE FROM observations WHERE id = ?", (observation_id,)
            )
            if self._vec_available:
                self.conn.execute(
                    "DELETE FROM vec_observations WHERE observation_id = ?",
                    (observation_id,),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return True

    def update_observation_embedding(
        self, observation_id: str, embedding: list[float]
    ) -> bool:
        """Replace an observation's embedding (and its vec row) only.

        The content / source_memory_ids are required positional args on
        ``update_observation`` (and ``content`` is NOT NULL), so re-embedding via
        that method risks clobbering the row. This narrow method updates just the
        vector and re-syncs vec_observations. Returns False if the id is unknown.

        Raises ValueError on a wrong-size vector — same rationale as
        update_memory_embedding (refuse to persist a mis-sized embedding).
        """
        assert self.conn is not None
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"update_observation_embedding expects a {EMBEDDING_DIM}-d vector, "
                f"got {len(embedding)}"
            )

        row = self.conn.execute(
            "SELECT bank_id FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        if not row:
            return False

        emb_blob = _serialize_vector(embedding)
        try:
            self.conn.execute(
                "UPDATE observations SET embedding = ? WHERE id = ?",
                (emb_blob, observation_id),
            )
            self._sync_vec_row(
                "vec_observations",
                "observation_id",
                observation_id,
                row["bank_id"],
                emb_blob,
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return True

    def get_observations(
        self, bank_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get observations for a bank, newest first."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT * FROM observations
               WHERE bank_id = ?
               ORDER BY updated_at DESC
               LIMIT ?""",
            (bank_id, limit),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "bank_id": r["bank_id"],
                "content": r["content"],
                "source_memory_ids": json.loads(r["source_memory_ids"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def get_observation_embeddings(
        self, bank_id: str
    ) -> list[dict[str, Any]]:
        """Load all observation embeddings for semantic search."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT id, content, embedding, updated_at, source_memory_ids
               FROM observations
               WHERE bank_id = ? AND embedding IS NOT NULL""",
            (bank_id,),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "content": r["content"],
                "embedding": _deserialize_vector(r["embedding"]),
                "timestamp": r["updated_at"],
                "source_memory_ids": json.loads(r["source_memory_ids"]),
            }
            for r in rows
        ]

    def _brute_force_search_observations(
        self, query_embedding: list[float], bank_id: str, top_k: int
    ) -> list[dict[str, Any]]:
        """Brute-force cosine search over a bank's observation embeddings."""
        candidates = self.get_observation_embeddings(bank_id)
        return semantic_search(query_embedding, candidates, top_k=top_k)

    def vec_search_observations(
        self, query_embedding: list[float], bank_id: str, top_k: int
    ) -> list[dict[str, Any]]:
        """Per-bank KNN search over observations via sqlite-vec.

        Mirrors ``vec_search`` for the observation layer. Result dicts match the
        ``get_observation_embeddings`` + ``semantic_search`` shape
        (id/content/timestamp/source_memory_ids/similarity) so recall display is
        unchanged. Falls back to brute force when the extension is unavailable,
        the KNN errors, or it under-returns relative to the bank's vec-eligible
        observation count.
        """
        assert self.conn is not None

        if not self._vec_available:
            return self._brute_force_search_observations(query_embedding, bank_id, top_k)

        try:
            query_blob = _serialize_vector(query_embedding)
            knn_rows = self.conn.execute(
                """SELECT observation_id, distance
                   FROM vec_observations
                   WHERE bank_id = ? AND embedding MATCH ?
                   ORDER BY distance
                   LIMIT ?""",
                (bank_id, query_blob, max(top_k, 1)),
            ).fetchall()
        except Exception as e:
            logger.warning(
                "vec_search_observations failed, falling back to brute-force: %s", e
            )
            return self._brute_force_search_observations(query_embedding, bank_id, top_k)

        if len(knn_rows) < top_k:
            bank_count = self.conn.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE bank_id = ? AND embedding IS NOT NULL "
                "AND length(embedding) = ?",
                (bank_id, EMBEDDING_DIM * 4),
            ).fetchone()[0]
            if len(knn_rows) < min(top_k, bank_count):
                return self._brute_force_search_observations(
                    query_embedding, bank_id, top_k
                )

        if not knn_rows:
            return []

        distances = {r["observation_id"]: r["distance"] for r in knn_rows}
        ids = list(distances.keys())
        placeholders = ",".join("?" for _ in ids)
        obs_rows = self.conn.execute(
            f"""SELECT id, content, updated_at, source_memory_ids
                FROM observations
                WHERE id IN ({placeholders})""",
            ids,
        ).fetchall()

        results = []
        for r in obs_rows:
            similarity = max(0.0, 1.0 - float(distances[r["id"]]))
            results.append({
                "id": r["id"],
                "content": r["content"],
                "timestamp": r["updated_at"],
                "source_memory_ids": json.loads(r["source_memory_ids"]),
                "similarity": similarity,
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    def get_unconsolidated_memories(
        self, bank_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get memories that haven't been consolidated yet."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT id, content, entities, keywords, context,
                      timestamp, created_at
               FROM memories
               WHERE bank_id = ? AND consolidated_at IS NULL
               ORDER BY timestamp ASC
               LIMIT ?""",
            (bank_id, limit),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "content": r["content"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
                "context": r["context"],
                "timestamp": r["timestamp"],
            }
            for r in rows
        ]

    def count_unconsolidated_memories(self, bank_id: str) -> int:
        """Count memories not yet consolidated.

        A cheap ``COUNT(*)`` that never hydrates rows, used to debounce
        auto-consolidation (only run it once enough memories have accumulated).
        """
        assert self.conn is not None

        return self.conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE bank_id = ? AND consolidated_at IS NULL",
            (bank_id,),
        ).fetchone()[0]

    def mark_memories_consolidated(
        self, memory_ids: list[str]
    ) -> None:
        """Mark memories as consolidated."""
        assert self.conn is not None

        now = datetime.now(timezone.utc).isoformat()
        for mid in memory_ids:
            self.conn.execute(
                "UPDATE memories SET consolidated_at = ? WHERE id = ?",
                (now, mid),
            )
        self.conn.commit()

    # ── Background Failure Log ────────────────────────────────────────

    def record_failure(self, bank_id: str, event: str, detail: str = "") -> None:
        """Record a background failure so it can be surfaced to the user.

        Background jobs (auto-consolidation, mental-model auto-refresh) swallow
        their errors by design — a hiccup must never fail the retain that
        triggered it. This log is the user-visible trace of those silent
        failures, shown by ``KIROK_stats``. Best-effort: recording itself never
        raises, and the log is capped at ``MAX_SYSTEM_EVENTS`` rows.
        """
        assert self.conn is not None

        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                "INSERT INTO system_events (bank_id, event, detail, created_at) "
                "VALUES (?, ?, ?, ?)",
                (bank_id, event, detail, now),
            )
            self.conn.execute(
                "DELETE FROM system_events WHERE id NOT IN "
                "(SELECT id FROM system_events ORDER BY id DESC LIMIT ?)",
                (MAX_SYSTEM_EVENTS,),
            )
            self.conn.commit()
        except Exception as e:
            logger.warning("Could not record failure event '%s': %s", event, e)
            try:
                self.conn.rollback()
            except Exception:
                pass

    def get_recent_failures(
        self, bank_id: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get recent background failures, newest first.

        ``bank_id=None`` returns failures across all banks.
        """
        assert self.conn is not None

        if bank_id is None:
            rows = self.conn.execute(
                "SELECT bank_id, event, detail, created_at FROM system_events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT bank_id, event, detail, created_at FROM system_events "
                "WHERE bank_id = ? ORDER BY id DESC LIMIT ?",
                (bank_id, limit),
            ).fetchall()

        return [
            {
                "bank_id": r["bank_id"],
                "event": r["event"],
                "detail": r["detail"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def clear_observations(self, bank_id: str) -> int:
        """Clear all observations for a bank. Returns count deleted."""
        assert self.conn is not None

        count = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE bank_id = ?", (bank_id,)
        ).fetchone()[0]

        if count > 0:
            self.conn.execute(
                "DELETE FROM fts_observations WHERE id IN "
                "(SELECT id FROM observations WHERE bank_id = ?)",
                (bank_id,),
            )
            if self._vec_available:
                self.conn.execute(
                    "DELETE FROM vec_observations WHERE bank_id = ?", (bank_id,)
                )
            self.conn.execute(
                "DELETE FROM observations WHERE bank_id = ?", (bank_id,)
            )
            # Reset consolidated_at so memories get re-consolidated
            self.conn.execute(
                "UPDATE memories SET consolidated_at = NULL WHERE bank_id = ?",
                (bank_id,),
            )
            self.conn.commit()

        return count
