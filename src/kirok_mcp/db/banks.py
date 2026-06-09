"""Bank management for MemoryDB: listing, stats, config, clearing,
deletion, and the background-failure log."""

import logging
from datetime import datetime, timezone
from typing import Any

from kirok_mcp.db.base import MAX_SYSTEM_EVENTS


logger = logging.getLogger("kirok.db")


class BankMixin:
    """Bank-level operations (expects self.conn, self._vec_available)."""

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
