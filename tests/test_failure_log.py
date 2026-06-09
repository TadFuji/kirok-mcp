"""Background-failure log: record, retrieve, prune (kirok_mcp.db.system_events).

WHY these tests exist: auto-consolidation and mental-model auto-refresh swallow
their errors by design (a hiccup must never fail the retain that triggered
them). The failure log is the only user-visible trace of those errors, so it
must reliably record, order, scope, and bound them.
"""

import shutil
import tempfile
from pathlib import Path

from kirok_mcp.db import MAX_SYSTEM_EVENTS, MemoryDB


def _make_db(tmp: str) -> MemoryDB:
    db = MemoryDB(db_path=Path(tmp) / "test.db")
    db.connect()
    return db


def test_record_and_get_failure_roundtrip():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        db.record_failure("bank-a", "auto_consolidation", "boom")
        failures = db.get_recent_failures("bank-a")
        assert len(failures) == 1
        f = failures[0]
        assert f["bank_id"] == "bank-a"
        assert f["event"] == "auto_consolidation"
        assert f["detail"] == "boom"
        assert f["created_at"]  # ISO timestamp present
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_failures_are_scoped_by_bank_and_newest_first():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        db.record_failure("bank-a", "first")
        db.record_failure("bank-b", "other-bank")
        db.record_failure("bank-a", "second")

        bank_a = db.get_recent_failures("bank-a")
        assert [f["event"] for f in bank_a] == ["second", "first"]

        all_banks = db.get_recent_failures()
        assert len(all_banks) == 3
        assert all_banks[0]["event"] == "second"
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_failure_log_is_capped():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        for i in range(MAX_SYSTEM_EVENTS + 25):
            db.record_failure("bank-a", f"event-{i}")

        count = db.conn.execute(
            "SELECT COUNT(*) FROM system_events"
        ).fetchone()[0]
        assert count == MAX_SYSTEM_EVENTS

        # The newest rows survive pruning, the oldest are gone
        newest = db.get_recent_failures("bank-a", limit=1)[0]
        assert newest["event"] == f"event-{MAX_SYSTEM_EVENTS + 24}"
        oldest_gone = db.conn.execute(
            "SELECT COUNT(*) FROM system_events WHERE event = 'event-0'"
        ).fetchone()[0]
        assert oldest_gone == 0
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_failure_never_raises():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        # Drop the table to simulate a broken database — recording must
        # degrade to a logged warning, never an exception (it runs inside
        # error handlers of background jobs).
        db.conn.execute("DROP TABLE system_events")
        # Passes iff this call returns instead of raising
        db.record_failure("bank-a", "auto_consolidation", "boom")
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
