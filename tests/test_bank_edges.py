"""Edge cases for bank management and config defaults.

WHY: clearing/deleting a bank that does not exist (or is already empty) is a
user-reachable path via KIROK_clear_bank / KIROK_delete_bank — it must report
zero counts, not error. get_bank_config promises defaults for unset banks; the
retain pipeline relies on that shape ('retain_mission' key always present).
"""

import shutil
import tempfile
from pathlib import Path

from kirok_mcp.db import MemoryDB


def _make_db(tmp: str) -> MemoryDB:
    db = MemoryDB(db_path=Path(tmp) / "test.db")
    db.connect()
    return db


def test_clear_bank_on_nonexistent_bank_returns_zero():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        result = db.clear_bank("no-such-bank")
        assert result == {"memories_deleted": 0, "observations_deleted": 0}
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_bank_on_nonexistent_bank_returns_zero():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        result = db.delete_bank("no-such-bank")
        assert result == {
            "memories_deleted": 0,
            "observations_deleted": 0,
            "models_deleted": 0,
            "config_deleted": 0,
        }
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_bank_removes_config_even_without_memories():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        # A bank can exist as config-only (configured before first retain)
        db.set_bank_config("config-only", retain_mission="track stuff")
        result = db.delete_bank("config-only")
        assert result["config_deleted"] == 1
        assert db.get_bank_config("config-only")["retain_mission"] == ""
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_bank_config_returns_defaults_when_unset():
    tmp = tempfile.mkdtemp()
    db = _make_db(tmp)
    try:
        config = db.get_bank_config("never-configured")
        assert config == {
            "bank_id": "never-configured",
            "retain_mission": "",
            "observations_mission": "",
        }
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
