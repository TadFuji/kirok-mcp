"""SQLite database layer for Kirok memory storage.

Manages memories, mental models, observations, bank configs, and
FTS5 full-text search indexes. Vectors are stored as binary blobs
for efficient retrieval.
"""

import json
import logging
import shutil
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import re


logger = logging.getLogger("kirok.db")


def _serialize_vector(vector: list[float]) -> bytes:
    """Serialize a float vector to bytes for SQLite BLOB storage."""
    return struct.pack(f"{len(vector)}f", *vector)


def _deserialize_vector(blob: bytes) -> list[float]:
    """Deserialize bytes back to a float vector."""
    n = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{n}f", blob))


# FTS5 special operators that must not appear as bare tokens in MATCH queries
_FTS5_OPERATORS = re.compile(r'\b(AND|OR|NOT|NEAR)\b', re.IGNORECASE)


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

    # Split into tokens and wrap each in double quotes
    tokens = cleaned.split()
    if not tokens:
        return None

    # Double-quote each token for safe literal matching
    quoted = ' '.join(f'"{t}"' for t in tokens)
    return quoted


def _resolve_db_path(db_path: str | Path | None) -> Path:
    """Resolve the database path with automatic migration from legacy locations.

    Priority:
    1. Explicit db_path argument (if provided)
    2. ~/.kirok/memory.db (new default)
    3. If ~/.kirok/ doesn't exist but ~/.hindsight/memory.db does,
       automatically copy it to ~/.kirok/ for seamless migration.
    """
    if db_path is not None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    KIROK_dir = Path.home() / ".Kirok"
    KIROK_db = KIROK_dir / "memory.db"
    legacy_dir = Path.home() / ".hindsight"
    legacy_db = legacy_dir / "memory.db"

    if KIROK_db.exists():
        return KIROK_db

    # Auto-migrate from legacy location
    if legacy_db.exists():
        KIROK_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Migrating database from %s to %s",
            legacy_db, KIROK_db,
        )
        shutil.copy2(str(legacy_db), str(KIROK_db))
        logger.info("Migration complete. Original database preserved at %s", legacy_db)
        return KIROK_db

    # Fresh install — create new
    KIROK_dir.mkdir(parents=True, exist_ok=True)
    return KIROK_db


class MemoryDB:
    """SQLite-backed memory database with FTS5 full-text search."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = _resolve_db_path(db_path)
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Open database connection and initialize schema."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

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
        """)

        # FTS5 virtual tables (created separately — cannot use IF NOT EXISTS
        # inside executescript for virtual tables on all SQLite versions)
        for ddl in [
            """CREATE VIRTUAL TABLE fts_memories USING fts5(
                id UNINDEXED, bank_id UNINDEXED,
                content, entities, keywords, context
            )""",
            """CREATE VIRTUAL TABLE fts_observations USING fts5(
                id UNINDEXED, bank_id UNINDEXED, content
            )""",
        ]:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # Already exists

        # Schema migrations for existing databases
        self._migrate_schema()

        self.conn.commit()

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

        return {
            "bank_id": bank_id,
            "memory_count": mem_count,
            "mental_model_count": model_count,
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

        self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.execute("DELETE FROM fts_memories WHERE id = ?", (memory_id,))
        self.conn.commit()
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

    def clear_bank(self, bank_id: str) -> int:
        """Delete all memories in a bank. Returns number of deleted memories."""
        assert self.conn is not None

        ids = self.conn.execute(
            "SELECT id FROM memories WHERE bank_id = ?", (bank_id,)
        ).fetchall()
        count = len(ids)

        if count > 0:
            self.conn.execute(
                "DELETE FROM fts_memories WHERE id IN "
                "(SELECT id FROM memories WHERE bank_id = ?)",
                (bank_id,),
            )
            self.conn.execute("DELETE FROM memories WHERE bank_id = ?", (bank_id,))
            self.conn.commit()

        return count

    def delete_bank(self, bank_id: str) -> dict[str, int]:
        """Delete a bank: all memories, FTS entries, and mental models."""
        assert self.conn is not None

        mem_count = self.clear_bank(bank_id)

        model_count = self.conn.execute(
            "SELECT COUNT(*) FROM mental_models WHERE bank_id = ?", (bank_id,)
        ).fetchone()[0]
        self.conn.execute(
            "DELETE FROM mental_models WHERE bank_id = ?", (bank_id,)
        )
        self.conn.commit()

        return {"memories_deleted": mem_count, "models_deleted": model_count}

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
