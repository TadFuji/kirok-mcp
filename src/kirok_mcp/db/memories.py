"""Memory CRUD and time-range browsing for MemoryDB."""

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from kirok_mcp.db.base import _serialize_vector
from kirok_mcp.embeddings import EMBEDDING_DIM


class MemoryMixin:
    """Memory row operations (expects self.conn, self._vec_available)."""

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
