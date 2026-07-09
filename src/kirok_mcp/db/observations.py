"""Observation CRUD for MemoryDB (consolidated-knowledge layer)."""

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from kirok_mcp.db.base import _serialize_vector
from kirok_mcp.embeddings import EMBEDDING_DIM


class ObservationMixin:
    """Observation row operations (expects self.conn, self._vec_available)."""

    def insert_observation(
        self,
        bank_id: str,
        content: str,
        source_memory_ids: list[str],
        embedding: list[float] | None = None,
        commit: bool = True,
    ) -> str:
        """Insert a new observation. Returns the observation ID.

        ``commit=False`` runs the writes without committing so a caller
        (consolidation) can batch several observation changes plus the
        memory-consolidated mark into a single atomic transaction.
        """
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

            if commit:
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
        commit: bool = True,
    ) -> bool:
        """Update an existing observation. Returns True if found.

        ``commit=False`` defers the commit so consolidation can batch this into
        one atomic transaction (see ``insert_observation``).
        """
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

            if commit:
                self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return True

    def deprecate_observation(
        self, observation_id: str, reason: str = "", commit: bool = True
    ) -> bool:
        """Soft-delete an observation: stamp ``deprecated_at`` and drop it from
        the FTS and vec search indexes, but keep the row.

        Consolidation uses this instead of ``delete_observation`` so a mistaken
        LLM delete does not irreversibly destroy knowledge — the row survives and
        an audit event records which observation was retired and why. A
        deprecated observation is excluded from every read path (search, listing,
        stats, consolidation's existing set), so it disappears from results just
        like a hard delete would. Returns True if a live observation was found.

        ``commit=False`` batches this into consolidation's single transaction;
        the audit event is written on the same connection so it can never persist
        without the deprecation (or vice versa).
        """
        assert self.conn is not None

        row = self.conn.execute(
            "SELECT bank_id FROM observations "
            "WHERE id = ? AND deprecated_at IS NULL",
            (observation_id,),
        ).fetchone()
        if not row:
            return False

        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                "UPDATE observations SET deprecated_at = ? WHERE id = ?",
                (now, observation_id),
            )
            self.conn.execute(
                "DELETE FROM fts_observations WHERE id = ?", (observation_id,)
            )
            if self._vec_available:
                self.conn.execute(
                    "DELETE FROM vec_observations WHERE observation_id = ?",
                    (observation_id,),
                )
            self._record_event(
                row["bank_id"],
                "observation_deprecated",
                f"{observation_id}: {reason}",
                commit=False,
            )

            if commit:
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
               WHERE bank_id = ? AND deprecated_at IS NULL
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
