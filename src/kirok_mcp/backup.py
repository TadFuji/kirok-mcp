"""Backup, export, and import for Kirok memory storage.

Offline tooling — works without a Gemini API key. Three operations:

- export:   dump all banks (memories, observations, mental models, bank
            configs) to a single JSON file, embeddings included (base64),
            so a backup never needs re-embedding on restore.
- import:   load such a JSON file into a database, skipping rows whose IDs
            already exist. Safe to re-run; never overwrites existing data.
- snapshot: byte-level copy of the live database via VACUUM INTO, safe to
            run while the MCP server holds the database open (WAL mode).

CLI entry point: ``kirok-backup`` (see ``main``).
"""

import argparse
import base64
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kirok_mcp.db import MemoryDB, _join_json_list, _resolve_db_path
from kirok_mcp.embeddings import EMBEDDING_DIM


logger = logging.getLogger("kirok.backup")

EXPORT_FORMAT = "kirok-export"
EXPORT_FORMAT_VERSION = 1

# Default destination for both JSON exports and DB snapshots. Lives next to
# the database itself so backups survive repo deletion but not disk loss —
# users should copy them to second storage for real disaster recovery.
DEFAULT_BACKUP_DIR = Path.home() / ".kirok" / "backups"


def _b64_or_none(blob: bytes | None) -> str | None:
    return base64.b64encode(blob).decode("ascii") if blob is not None else None


def _blob_or_none(encoded: str | None) -> bytes | None:
    return base64.b64decode(encoded) if encoded is not None else None


# ── Export ────────────────────────────────────────────────────────────


def export_data(db: MemoryDB) -> dict[str, Any]:
    """Export every bank's data to a JSON-serializable dict.

    Rows are exported column-for-column (JSON columns stay as their stored
    strings, embeddings become base64) so an import can reproduce the
    original rows byte-for-byte.
    """
    assert db.conn is not None

    # Read every table inside one transaction so a concurrent WAL writer
    # can't leave the export with an inconsistent cross-table snapshot.
    # Guard against already being inside a transaction (e.g. nested calls).
    own_transaction = not db.conn.in_transaction
    if own_transaction:
        db.conn.execute("BEGIN")
    try:
        memories = [
            {
                "id": r["id"],
                "bank_id": r["bank_id"],
                "content": r["content"],
                "entities": r["entities"],
                "keywords": r["keywords"],
                "context": r["context"],
                "embedding": _b64_or_none(r["embedding"]),
                "timestamp": r["timestamp"],
                "created_at": r["created_at"],
                "metadata": r["metadata"],
                "consolidated_at": r["consolidated_at"],
            }
            for r in db.conn.execute("SELECT * FROM memories")
        ]

        observations = [
            {
                "id": r["id"],
                "bank_id": r["bank_id"],
                "content": r["content"],
                "source_memory_ids": r["source_memory_ids"],
                "embedding": _b64_or_none(r["embedding"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "deprecated_at": r["deprecated_at"],
            }
            for r in db.conn.execute("SELECT * FROM observations")
        ]

        mental_models = [
            {
                "id": r["id"],
                "bank_id": r["bank_id"],
                "topic": r["topic"],
                "insight": r["insight"],
                "based_on": r["based_on"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "auto_refresh": r["auto_refresh"],
                "source_query": r["source_query"],
            }
            for r in db.conn.execute("SELECT * FROM mental_models")
        ]

        bank_config = [
            {
                "bank_id": r["bank_id"],
                "retain_mission": r["retain_mission"],
                "observations_mission": r["observations_mission"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in db.conn.execute("SELECT * FROM bank_config")
        ]
    except Exception:
        if own_transaction:
            db.conn.rollback()
        raise
    else:
        if own_transaction:
            db.conn.commit()

    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "embedding_dim": EMBEDDING_DIM,
        "source_db": str(db.db_path),
        "counts": {
            "memories": len(memories),
            "observations": len(observations),
            "mental_models": len(mental_models),
            "bank_config": len(bank_config),
        },
        "memories": memories,
        "observations": observations,
        "mental_models": mental_models,
        "bank_config": bank_config,
    }


def export_to_file(db: MemoryDB, out_path: Path) -> dict[str, int]:
    """Export the database to ``out_path`` (JSON). Refuses to overwrite."""
    if out_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {out_path}")

    data = export_data(db)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return dict(data["counts"])


# ── Import ────────────────────────────────────────────────────────────


def import_data(db: MemoryDB, data: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Import an export dict into ``db``, skipping IDs that already exist.

    All inserts run in a single transaction — a failure rolls everything
    back, leaving the target database untouched. FTS rows are written
    alongside each memory/observation; the vec tables are reconciled
    afterwards via the existing self-heal migrations (they rebuild on any
    id-set drift), so vector search works immediately after import.
    """
    assert db.conn is not None

    if data.get("format") != EXPORT_FORMAT:
        raise ValueError(
            "Not a Kirok export file (missing format marker "
            f"'{EXPORT_FORMAT}')."
        )
    version = data.get("format_version")
    if version != EXPORT_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported export format_version {version} "
            f"(this build reads version {EXPORT_FORMAT_VERSION})."
        )
    if data.get("embedding_dim") not in (None, EMBEDDING_DIM):
        # Vectors of a different width import fine but are excluded from the
        # vec index (same rule as inserts); search falls back to brute force.
        logger.warning(
            "Export embedding_dim %s differs from current %s; "
            "imported vectors stay on the brute-force search path.",
            data.get("embedding_dim"),
            EMBEDDING_DIM,
        )

    existing_memories = {
        r["id"] for r in db.conn.execute("SELECT id FROM memories")
    }
    existing_observations = {
        r["id"] for r in db.conn.execute("SELECT id FROM observations")
    }
    existing_models = {
        r["id"] for r in db.conn.execute("SELECT id FROM mental_models")
    }
    existing_configs = {
        r["bank_id"] for r in db.conn.execute("SELECT bank_id FROM bank_config")
    }

    result = {
        table: {"inserted": 0, "skipped": 0}
        for table in ("memories", "observations", "mental_models", "bank_config")
    }

    try:
        db.conn.execute("BEGIN")

        for m in data.get("memories", []):
            if m["id"] in existing_memories:
                result["memories"]["skipped"] += 1
                continue
            db.conn.execute(
                """INSERT INTO memories
                   (id, bank_id, content, entities, keywords, context,
                    embedding, timestamp, created_at, metadata, consolidated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    m["id"], m["bank_id"], m["content"],
                    m["entities"], m["keywords"], m["context"],
                    _blob_or_none(m["embedding"]),
                    m["timestamp"], m["created_at"], m["metadata"],
                    m.get("consolidated_at"),
                ),
            )
            db.conn.execute(
                """INSERT INTO fts_memories
                   (id, bank_id, content, entities, keywords, context)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    m["id"], m["bank_id"], m["content"],
                    _join_json_list(m["entities"]),
                    _join_json_list(m["keywords"]),
                    m["context"],
                ),
            )
            result["memories"]["inserted"] += 1

        for o in data.get("observations", []):
            if o["id"] in existing_observations:
                result["observations"]["skipped"] += 1
                continue
            # .get(): exports written before the deprecated_at column exist
            deprecated_at = o.get("deprecated_at")
            db.conn.execute(
                """INSERT INTO observations
                   (id, bank_id, content, source_memory_ids, embedding,
                    created_at, updated_at, deprecated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    o["id"], o["bank_id"], o["content"],
                    o["source_memory_ids"], _blob_or_none(o["embedding"]),
                    o["created_at"], o["updated_at"], deprecated_at,
                ),
            )
            # Deprecated observations stay out of the search indexes
            if deprecated_at is None:
                db.conn.execute(
                    "INSERT INTO fts_observations (id, bank_id, content) "
                    "VALUES (?, ?, ?)",
                    (o["id"], o["bank_id"], o["content"]),
                )
            result["observations"]["inserted"] += 1

        for mm in data.get("mental_models", []):
            if mm["id"] in existing_models:
                result["mental_models"]["skipped"] += 1
                continue
            db.conn.execute(
                """INSERT INTO mental_models
                   (id, bank_id, topic, insight, based_on,
                    created_at, updated_at, auto_refresh, source_query)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mm["id"], mm["bank_id"], mm["topic"], mm["insight"],
                    mm["based_on"], mm["created_at"], mm["updated_at"],
                    mm.get("auto_refresh"), mm.get("source_query"),
                ),
            )
            result["mental_models"]["inserted"] += 1

        for c in data.get("bank_config", []):
            if c["bank_id"] in existing_configs:
                result["bank_config"]["skipped"] += 1
                continue
            db.conn.execute(
                """INSERT INTO bank_config
                   (bank_id, retain_mission, observations_mission,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    c["bank_id"], c["retain_mission"],
                    c["observations_mission"],
                    c["created_at"], c["updated_at"],
                ),
            )
            result["bank_config"]["inserted"] += 1

        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise

    # Reconcile the vec indexes with the freshly imported rows. These
    # migrations rebuild on any id-set drift and are no-ops otherwise.
    db._migrate_vectors_to_vec_table()
    db._migrate_observation_vectors()

    return result


def import_from_file(db: MemoryDB, in_path: Path) -> dict[str, dict[str, int]]:
    """Import a JSON export file into ``db`` (see ``import_data``)."""
    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return import_data(db, data)


# ── Snapshot ──────────────────────────────────────────────────────────


def snapshot_db(db_path: Path, out_path: Path) -> Path:
    """Copy the database file via VACUUM INTO and verify the copy.

    VACUUM INTO takes a consistent read snapshot, so it is safe while the
    MCP server holds the database open in WAL mode. The copy is verified
    with PRAGMA integrity_check before this returns. Refuses to overwrite.
    """
    if out_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {out_path}")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        src = sqlite3.connect(str(db_path), timeout=60)
        try:
            src.execute("VACUUM INTO ?", (str(out_path),))
        finally:
            src.close()

        copy = sqlite3.connect(str(out_path))
        try:
            status = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
        if status != "ok":
            raise RuntimeError(
                f"Snapshot failed integrity check ({status}): {out_path}"
            )
    except BaseException:
        # A broken half-written copy must not survive: auto_snapshot gates on
        # the newest auto file's mtime, so leaving it would suppress the next
        # good snapshot for a full interval.
        out_path.unlink(missing_ok=True)
        raise
    return out_path


# ── Auto-snapshot (startup) ──────────────────────────────────────────

# Naming distinguishes automatic snapshots from manual ``kirok-backup
# snapshot`` runs (``memory-snapshot-*.db``) so rotation only ever deletes
# what it created.
AUTO_SNAPSHOT_PREFIX = "memory-auto-"
AUTO_SNAPSHOT_GLOB = f"{AUTO_SNAPSHOT_PREFIX}*.db"


def _auto_snapshot_files(backup_dir: Path) -> list[Path]:
    """Auto-snapshot files in ``backup_dir``, newest first."""
    return sorted(
        backup_dir.glob(AUTO_SNAPSHOT_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def rotate_auto_snapshots(backup_dir: Path, keep: int) -> None:
    """Delete auto-snapshots beyond the newest ``keep``.

    Only matches ``AUTO_SNAPSHOT_GLOB`` — manual snapshots and JSON exports
    use different filenames and are never touched.
    """
    for old in _auto_snapshot_files(backup_dir)[keep:]:
        old.unlink()


def auto_snapshot(
    db_path: Path,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    *,
    interval_hours: float = 24,
    keep: int = 5,
) -> Path | None:
    """Create a timestamped auto-snapshot if the last one is stale enough.

    Returns the new snapshot path, or ``None`` if skipped: disabled
    (``interval_hours <= 0``) or the newest existing auto-snapshot is younger
    than ``interval_hours``. Raises on snapshot failure (VACUUM INTO /
    integrity check) — the caller decides how to log it.
    """
    if interval_hours <= 0:
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    existing = _auto_snapshot_files(backup_dir)
    if existing:
        age_hours = (time.time() - existing[0].stat().st_mtime) / 3600
        if age_hours < interval_hours:
            return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = backup_dir / f"{AUTO_SNAPSHOT_PREFIX}{stamp}.db"
    snapshot_db(db_path, out)
    rotate_auto_snapshots(backup_dir, keep)
    return out


# ── CLI ───────────────────────────────────────────────────────────────


def _default_db_path(arg: str | None) -> Path:
    """CLI database path: --db flag > KIROK_DB_PATH env > ~/.kirok/memory.db."""
    return _resolve_db_path(arg or os.environ.get("KIROK_DB_PATH") or None)


def main(argv: list[str] | None = None) -> int:
    """Run the kirok-backup CLI. Returns a process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="kirok-backup",
        description="Backup, export, and import Kirok memory data.",
    )
    parser.add_argument(
        "--db",
        help="Path to memory.db (default: KIROK_DB_PATH env or ~/.kirok/memory.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Export all banks to a JSON file")
    p_export.add_argument(
        "--out", help="Output file (default: ~/.kirok/backups/kirok-export-<timestamp>.json)"
    )

    p_import = sub.add_parser(
        "import", help="Import a JSON export (existing IDs are skipped)"
    )
    p_import.add_argument("file", help="JSON export file to import")

    p_snapshot = sub.add_parser(
        "snapshot", help="Copy the database file safely (VACUUM INTO)"
    )
    p_snapshot.add_argument(
        "--out", help="Output file (default: ~/.kirok/backups/memory-snapshot-<timestamp>.db)"
    )

    args = parser.parse_args(argv)
    db_path = _default_db_path(args.db)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    try:
        if args.command == "snapshot":
            out = Path(args.out) if args.out else (
                DEFAULT_BACKUP_DIR / f"memory-snapshot-{stamp}.db"
            )
            snapshot_db(db_path, out)
            size_mb = out.stat().st_size / (1024 * 1024)
            print(f"Snapshot OK: {out} ({size_mb:.1f} MB, integrity ok)")
            return 0

        db = MemoryDB(db_path=db_path)
        db.connect()
        try:
            if args.command == "export":
                out = Path(args.out) if args.out else (
                    DEFAULT_BACKUP_DIR / f"kirok-export-{stamp}.json"
                )
                counts = export_to_file(db, out)
                print(f"Export OK: {out}")
                for table, count in counts.items():
                    print(f"- {table}: {count}")
                return 0

            if args.command == "import":
                result = import_from_file(db, Path(args.file))
                print(f"Import OK into: {db_path}")
                for table, c in result.items():
                    print(
                        f"- {table}: inserted {c['inserted']}, "
                        f"skipped {c['skipped']} (already present)"
                    )
                return 0
        finally:
            db.close()
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
