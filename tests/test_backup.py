"""Export / import / snapshot roundtrip tests for kirok_mcp.backup."""

import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from kirok_mcp.backup import (
    auto_snapshot,
    export_data,
    export_to_file,
    import_data,
    import_from_file,
    rotate_auto_snapshots,
    snapshot_db,
)
from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import EMBEDDING_DIM


def _make_vec(seed: float = 0.1) -> list[float]:
    return [seed + i * 1e-4 for i in range(EMBEDDING_DIM)]


def _make_db(tmp: str, name: str = "test.db") -> MemoryDB:
    db = MemoryDB(db_path=Path(tmp) / name)
    db.connect()
    return db


def _populate(db: MemoryDB) -> dict[str, str]:
    """Insert one row of every kind; return the generated IDs."""
    mem_id = db.insert_memory(
        bank_id="bank-a",
        content="藤川さんは丁寧な進行を好む",
        embedding=_make_vec(0.1),
        entities=["藤川"],
        keywords=["preference", "進行"],
        context="unit test",
        metadata={"source": "test"},
    )
    obs_id = db.insert_observation(
        bank_id="bank-a",
        content="consolidated insight about 藤川",
        source_memory_ids=[mem_id],
        embedding=_make_vec(0.2),
    )
    model_id = db.insert_mental_model_with_options(
        bank_id="bank-a",
        topic="working style",
        insight="careful, stepwise",
        based_on=[mem_id],
        auto_refresh=True,
        source_query="how does 藤川 work",
    )
    db.set_bank_config(
        bank_id="bank-a",
        retain_mission="track preferences",
        observations_mission="synthesize habits",
    )
    return {"memory": mem_id, "observation": obs_id, "model": model_id}


# ── Export ────────────────────────────────────────────────────────────


def test_export_counts_and_format():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        _populate(db)
        data = export_data(db)
        assert data["format"] == "kirok-export"
        assert data["format_version"] == 1
        assert data["embedding_dim"] == EMBEDDING_DIM
        assert data["counts"] == {
            "memories": 1,
            "observations": 1,
            "mental_models": 1,
            "bank_config": 1,
        }
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_to_file_refuses_overwrite():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        out = Path(tmp) / "export.json"
        export_to_file(db, out)
        with pytest.raises(FileExistsError):
            export_to_file(db, out)
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ── Import roundtrip ──────────────────────────────────────────────────


def test_roundtrip_preserves_all_rows():
    tmp = tempfile.mkdtemp()
    src = _make_db(tmp, "src.db")
    dst = _make_db(tmp, "dst.db")
    try:
        ids = _populate(src)
        out = Path(tmp) / "export.json"
        export_to_file(src, out)

        result = import_from_file(dst, out)
        assert result["memories"]["inserted"] == 1
        assert result["observations"]["inserted"] == 1
        assert result["mental_models"]["inserted"] == 1
        assert result["bank_config"]["inserted"] == 1

        # Memory row survives byte-for-byte (id, content, metadata, embedding)
        mem = dst.get_memory(ids["memory"])
        assert mem is not None
        assert mem["content"] == "藤川さんは丁寧な進行を好む"
        assert mem["entities"] == ["藤川"]
        assert mem["metadata"] == {"source": "test"}
        src_blob = src.conn.execute(
            "SELECT embedding FROM memories WHERE id = ?", (ids["memory"],)
        ).fetchone()[0]
        dst_blob = dst.conn.execute(
            "SELECT embedding FROM memories WHERE id = ?", (ids["memory"],)
        ).fetchone()[0]
        assert src_blob == dst_blob

        # Observation and mental model (including auto_refresh / source_query)
        obs = dst.get_observations("bank-a")
        assert len(obs) == 1 and obs[0]["id"] == ids["observation"]
        model = dst.get_mental_model(ids["model"])
        assert model is not None
        assert model["auto_refresh"] is True
        assert model["source_query"] == "how does 藤川 work"

        # Bank config
        config = dst.get_bank_config("bank-a")
        assert config["retain_mission"] == "track preferences"
        assert config["observations_mission"] == "synthesize habits"
    finally:
        src.close()
        dst.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_skips_existing_ids():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        _populate(db)
        data = export_data(db)
        # Importing into the same database must be a clean no-op
        result = import_data(db, data)
        for table in ("memories", "observations", "mental_models", "bank_config"):
            assert result[table]["inserted"] == 0
            assert result[table]["skipped"] == 1
        assert db.get_stats("bank-a")["memory_count"] == 1
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_rebuilds_search_indexes():
    tmp = tempfile.mkdtemp()
    src = _make_db(tmp, "src.db")
    dst = _make_db(tmp, "dst.db")
    try:
        ids = _populate(src)
        data = export_data(src)
        import_data(dst, data)

        # FTS finds the imported memory by its Japanese content
        hits = dst.fts_search("bank-a", "丁寧な進行")
        assert any(h["id"] == ids["memory"] for h in hits)

        # Vector search returns the imported memory (vec or brute-force path)
        results = dst.vec_search(_make_vec(0.1), "bank-a", top_k=1)
        assert results and results[0]["id"] == ids["memory"]

        if dst._vec_available:
            vec_count = dst.conn.execute(
                "SELECT COUNT(*) FROM vec_memories"
            ).fetchone()[0]
            assert vec_count == 1
    finally:
        src.close()
        dst.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_rejects_foreign_json():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        with pytest.raises(ValueError):
            import_data(db, {"format": "something-else", "format_version": 1})
        with pytest.raises(ValueError):
            import_data(db, {"format": "kirok-export", "format_version": 99})
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ── Snapshot ──────────────────────────────────────────────────────────


def test_snapshot_creates_verified_copy():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        _populate(db)
        out = Path(tmp) / "snapshot.db"
        # Source connection stays open — mirrors a live MCP server
        snapshot_db(db.db_path, out)

        copy = sqlite3.connect(str(out))
        try:
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            count = copy.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            assert count == 1
        finally:
            copy.close()

        with pytest.raises(FileExistsError):
            snapshot_db(db.db_path, out)
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_missing_source():
    tmp = tempfile.mkdtemp()
    try:
        with pytest.raises(FileNotFoundError):
            snapshot_db(Path(tmp) / "nope.db", Path(tmp) / "out.db")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Auto-snapshot ─────────────────────────────────────────────────────


def test_auto_snapshot_creates_file():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        _populate(db)
        backup_dir = Path(tmp) / "backups"
        out = auto_snapshot(db.db_path, backup_dir, interval_hours=24, keep=5)
        assert out is not None
        assert out.exists()
        assert out.name.startswith("memory-auto-")
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_auto_snapshot_skips_when_recent():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        _populate(db)
        backup_dir = Path(tmp) / "backups"
        first = auto_snapshot(db.db_path, backup_dir, interval_hours=24, keep=5)
        assert first is not None

        second = auto_snapshot(db.db_path, backup_dir, interval_hours=24, keep=5)
        assert second is None
        # Still just the one file — no second snapshot was created.
        assert len(list(backup_dir.glob("memory-auto-*.db"))) == 1
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_auto_snapshot_disabled_by_zero_hours():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        _populate(db)
        backup_dir = Path(tmp) / "backups"
        out = auto_snapshot(db.db_path, backup_dir, interval_hours=0, keep=5)
        assert out is None
        assert not backup_dir.exists()
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_rotate_auto_snapshots_keeps_only_auto_files():
    tmp = tempfile.mkdtemp()
    backup_dir = Path(tmp)
    try:
        # Six auto snapshots with distinct mtimes (oldest to newest).
        auto_paths = []
        now = time.time()
        for i in range(6):
            p = backup_dir / f"memory-auto-2026070{i}-000000.db"
            p.write_bytes(b"x")
            os.utime(p, (now - (6 - i) * 100, now - (6 - i) * 100))
            auto_paths.append(p)

        # A manual snapshot and a JSON export must never be touched.
        manual = backup_dir / "memory-snapshot-20260701-000000.db"
        manual.write_bytes(b"manual")
        export = backup_dir / "kirok-export-20260701-000000.json"
        export.write_text("{}")

        rotate_auto_snapshots(backup_dir, keep=5)

        remaining_auto = sorted(backup_dir.glob("memory-auto-*.db"))
        assert len(remaining_auto) == 5
        # The oldest auto snapshot (index 0) was deleted; the rest survive.
        assert auto_paths[0] not in remaining_auto
        for p in auto_paths[1:]:
            assert p in remaining_auto

        assert manual.exists()
        assert export.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -- Review fixes: deprecated_at roundtrip, broken-snapshot cleanup ----------


def test_import_preserves_deprecated_observations():
    tmp = tempfile.mkdtemp()
    src = _make_db(tmp, "src.db")
    dst = _make_db(tmp, "dst.db")
    try:
        ids = _populate(src)
        src.deprecate_observation(ids["observation"], reason="unit test")
        data = export_data(src)
        assert data["observations"][0]["deprecated_at"] is not None

        import_data(dst, data)
        row = dst.conn.execute(
            "SELECT deprecated_at FROM observations WHERE id = ?",
            (ids["observation"],),
        ).fetchone()
        assert row is not None and row["deprecated_at"] is not None
        # Excluded from reads and from the FTS index
        assert dst.get_observations("bank-a") == []
        fts = dst.conn.execute(
            "SELECT COUNT(*) FROM fts_observations WHERE id = ?",
            (ids["observation"],),
        ).fetchone()[0]
        assert fts == 0
    finally:
        src.close()
        dst.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_accepts_pre_deprecation_exports():
    tmp = tempfile.mkdtemp()
    src = _make_db(tmp, "src.db")
    dst = _make_db(tmp, "dst.db")
    try:
        _populate(src)
        data = export_data(src)
        for obs in data["observations"]:
            del obs["deprecated_at"]  # export written by an older version

        import_data(dst, data)
        assert len(dst.get_observations("bank-a")) == 1
    finally:
        src.close()
        dst.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_failed_snapshot_leaves_no_output_file():
    tmp = tempfile.mkdtemp()
    try:
        bad_db = Path(tmp) / "garbage.db"
        bad_db.write_bytes(b"this is not a sqlite database at all")
        out = Path(tmp) / "memory-auto-19700101-000000.db"
        with pytest.raises(sqlite3.DatabaseError):
            snapshot_db(bad_db, out)
        assert not out.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
