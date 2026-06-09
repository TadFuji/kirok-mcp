import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# The FTS rebuild lives in the schema submodule; patches must target the
# namespace the rebuild actually calls into.
import kirok_mcp.db.schema as dbmod
from kirok_mcp.db import MemoryDB


def _downgrade_fts_to_unicode61(path: Path) -> None:
    """Replace the trigram FTS tables with empty unicode61 ones.

    Simulates a database created before the trigram migration so a fresh
    MemoryDB.connect() exercises the rebuild-from-source path.
    """
    raw = sqlite3.connect(str(path))
    try:
        raw.execute("DROP TABLE IF EXISTS fts_memories")
        raw.execute("DROP TABLE IF EXISTS fts_observations")
        raw.execute(
            "CREATE VIRTUAL TABLE fts_memories USING fts5("
            "id UNINDEXED, bank_id UNINDEXED, content, entities, keywords, context)"
        )
        raw.execute(
            "CREATE VIRTUAL TABLE fts_observations USING fts5("
            "id UNINDEXED, bank_id UNINDEXED, content)"
        )
        raw.commit()
    finally:
        raw.close()


class FtsTrigramTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "memory.db"
        self.db = MemoryDB(self.path)
        self.db.connect()

    def tearDown(self) -> None:
        self.db.close()
        self.tmpdir.cleanup()

    def test_new_db_uses_trigram_tokenizer(self) -> None:
        self.assertTrue(self.db._fts_is_trigram("fts_memories"))
        self.assertTrue(self.db._fts_is_trigram("fts_observations"))

    def test_japanese_substring_hits_with_trigram(self) -> None:
        mid = self.db.insert_memory(
            "bank",
            "来週の開発合宿は京都で開催します。",
            embedding=[1.0, 0.0],
            entities=["開発合宿"],
            keywords=["京都"],
        )

        # 3+ char Japanese substring matches (the core trigram win).
        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "開発合宿")], [mid]
        )
        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "開催")], []
        )  # 2 chars: dropped by sanitizer, left to semantic search

    def test_three_char_boundary(self) -> None:
        mid = self.db.insert_memory(
            "bank", "東京都の天気は晴れです。", embedding=[1.0, 0.0]
        )
        # 3 chars: tokenized and matchable.
        self.assertEqual([r["id"] for r in self.db.fts_search("bank", "東京都")], [mid])
        # 2 chars: dropped by the sanitizer (trigram cannot index < 3 chars).
        self.assertEqual(self.db.fts_search("bank", "東京"), [])

    def test_english_query_regression_and_case_folding(self) -> None:
        mid = self.db.insert_memory(
            "bank",
            "The deploy pipeline uses GitHub Actions.",
            embedding=[1.0, 0.0],
            entities=["GitHub Actions"],
            keywords=["deploy", "pipeline"],
        )

        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "deploy pipeline")], [mid]
        )
        # trigram folds case by default.
        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "DEPLOY")], [mid]
        )

    def test_migration_rebuilds_unicode61_as_trigram_with_backfill(self) -> None:
        mid = self.db.insert_memory(
            "bank",
            "来週の開発合宿は京都で開催します。",
            embedding=[1.0, 0.0],
            entities=["開発合宿", "設計レビュー"],
            keywords=["京都"],
        )
        oid = self.db.insert_observation(
            "bank", "藤川さんは開発合宿を好む。", [mid], embedding=[1.0, 0.0]
        )
        mem_total = self.db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        obs_total = self.db.conn.execute(
            "SELECT COUNT(*) FROM observations"
        ).fetchone()[0]
        self.db.close()

        _downgrade_fts_to_unicode61(self.path)

        self.db = MemoryDB(self.path)
        self.db.connect()  # triggers _migrate_fts_schema

        # Rebuilt as trigram.
        self.assertTrue(self.db._fts_is_trigram("fts_memories"))
        self.assertTrue(self.db._fts_is_trigram("fts_observations"))

        # Every source row was backfilled.
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM fts_memories").fetchone()[0],
            mem_total,
        )
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM fts_observations").fetchone()[0],
            obs_total,
        )

        # entities are space-joined, not stored as raw JSON.
        stored_entities = self.db.conn.execute(
            "SELECT entities FROM fts_memories WHERE id = ?", (mid,)
        ).fetchone()[0]
        self.assertEqual(stored_entities, "開発合宿 設計レビュー")

        # Japanese search works after the rebuild (memory + observation).
        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "開発合宿")], [mid]
        )
        self.assertEqual(oid, oid)  # observation backfilled (count asserted above)

    def test_migration_is_idempotent(self) -> None:
        self.db.insert_memory("bank", "開発合宿のメモ", embedding=[1.0, 0.0])
        # Already trigram; a second migration pass must be a no-op.
        self.db._migrate_fts_schema()
        self.assertTrue(self.db._fts_is_trigram("fts_memories"))
        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "開発合宿")],
            [r["id"] for r in self.db.fts_search("bank", "開発合宿")],
        )

    def test_migration_retries_after_backfill_failure(self) -> None:
        mid = self.db.insert_memory(
            "bank", "開発合宿のテスト記憶", embedding=[1.0, 0.0], entities=["開発合宿"]
        )
        self.db.close()
        _downgrade_fts_to_unicode61(self.path)

        # Force the backfill to fail mid-rebuild.
        with patch.object(dbmod, "_join_json_list", side_effect=RuntimeError("boom")):
            failed = MemoryDB(self.path)
            with self.assertRaises(RuntimeError):
                failed.connect()
            failed.close()

        # The FTS table must still be unicode61 (the half-built trigram table was
        # rolled back), so the next connect retries rather than skipping.
        raw = sqlite3.connect(str(self.path))
        sql = raw.execute(
            "SELECT sql FROM sqlite_master WHERE name='fts_memories'"
        ).fetchone()[0]
        raw.close()
        self.assertNotIn("trigram", sql.lower())

        # A clean reconnect completes the migration and backfills.
        self.db = MemoryDB(self.path)
        self.db.connect()
        self.assertTrue(self.db._fts_is_trigram("fts_memories"))
        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "開発合宿")], [mid]
        )

    def test_migration_tolerates_malformed_entities_json(self) -> None:
        mid = self.db.insert_memory(
            "bank",
            "壊れたエンティティを持つ記憶テスト",
            embedding=[1.0, 0.0],
            entities=["正常"],
            keywords=[],
        )
        self.db.close()

        # Corrupt the entities JSON, then downgrade FTS so the rebuild must
        # backfill this row.
        raw = sqlite3.connect(str(self.path))
        raw.execute("UPDATE memories SET entities = ? WHERE id = ?", ("not-json", mid))
        raw.commit()
        raw.close()
        _downgrade_fts_to_unicode61(self.path)

        self.db = MemoryDB(self.path)
        self.db.connect()  # must not crash on the malformed row

        # Row is still indexed (content searchable); entities fell back to empty.
        self.assertEqual(
            [r["id"] for r in self.db.fts_search("bank", "記憶テスト")], [mid]
        )
        stored_entities = self.db.conn.execute(
            "SELECT entities FROM fts_memories WHERE id = ?", (mid,)
        ).fetchone()[0]
        self.assertEqual(stored_entities, "")


if __name__ == "__main__":
    unittest.main()
