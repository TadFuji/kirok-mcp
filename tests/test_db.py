import tempfile
import unittest
from pathlib import Path

from kirok_mcp.db import MemoryDB, _sanitize_fts_query


class MemoryDBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = MemoryDB(Path(self.tmpdir.name) / "memory.db")
        self.db.connect()

    def tearDown(self) -> None:
        self.db.close()
        self.tmpdir.cleanup()

    def test_memory_crud_and_fts_index_updates(self) -> None:
        memory_id = self.db.insert_memory(
            bank_id="project",
            content="The deploy pipeline uses GitHub Actions.",
            embedding=[1.0, 0.0],
            entities=["GitHub Actions"],
            keywords=["deploy", "pipeline"],
            context="architecture decision",
            timestamp="2026-05-04T00:00:00+00:00",
        )

        memory = self.db.get_memory(memory_id)
        self.assertIsNotNone(memory)
        self.assertEqual(memory["bank_id"], "project")
        self.assertEqual(memory["entities"], ["GitHub Actions"])

        results = self.db.fts_search("project", "deploy pipeline")
        self.assertEqual([r["id"] for r in results], [memory_id])

        updated = self.db.update_memory(
            memory_id,
            content="The release process uses Buildkite.",
            entities=["Buildkite"],
            keywords=["release"],
            embedding=[0.0, 1.0],
        )
        self.assertTrue(updated)

        self.assertEqual(self.db.fts_search("project", "deploy pipeline"), [])
        updated_results = self.db.fts_search("project", "Buildkite release")
        self.assertEqual([r["id"] for r in updated_results], [memory_id])

        self.assertTrue(self.db.delete_memory(memory_id))
        self.assertIsNone(self.db.get_memory(memory_id))
        self.assertEqual(self.db.fts_search("project", "Buildkite"), [])

    def test_fts_query_sanitization_handles_special_syntax(self) -> None:
        # Tokens shorter than 3 chars are dropped for the trigram tokenizer
        # ('03', '25', 'x', 'y' here), leaving only the >=3-char tokens.
        sanitized = _sanitize_fts_query('2026-03-25 ASCII AND NEAR("x" "y") * ^')

        self.assertEqual(sanitized, '"2026" "ASCII"')
        self.assertIsNone(_sanitize_fts_query(" AND - * ^ "))

    def test_clear_bank_removes_memories_and_observations_only(self) -> None:
        memory_id = self.db.insert_memory(
            "bank-a",
            "alpha memory",
            embedding=[1.0, 0.0],
            entities=["Alpha"],
            keywords=["memory"],
        )
        self.db.insert_observation(
            "bank-a",
            "alpha observation",
            [memory_id],
            embedding=[1.0, 0.0],
        )
        model_id = self.db.insert_mental_model(
            "bank-a",
            "alpha model",
            "alpha insight",
            [memory_id],
        )
        self.db.set_bank_config("bank-a", "retain mission", "observation mission")

        result = self.db.clear_bank("bank-a")

        self.assertEqual(
            result,
            {"memories_deleted": 1, "observations_deleted": 1},
        )
        self.assertEqual(self.db.list_memories("bank-a"), [])
        self.assertEqual(self.db.get_observations("bank-a"), [])
        self.assertIsNotNone(self.db.get_mental_model(model_id))
        self.assertEqual(
            self.db.get_bank_config("bank-a")["retain_mission"],
            "retain mission",
        )

    def test_delete_bank_removes_all_bank_data(self) -> None:
        memory_id = self.db.insert_memory(
            "bank-a",
            "alpha memory",
            embedding=[1.0, 0.0],
            entities=["Alpha"],
            keywords=["memory"],
        )
        model_id = self.db.insert_mental_model(
            "bank-a",
            "alpha model",
            "alpha insight",
            [memory_id],
        )
        self.db.insert_observation(
            "bank-a",
            "alpha observation",
            [memory_id],
            embedding=[1.0, 0.0],
        )
        self.db.set_bank_config("bank-a", "retain mission", "observation mission")

        result = self.db.delete_bank("bank-a")

        self.assertEqual(
            result,
            {
                "memories_deleted": 1,
                "observations_deleted": 1,
                "models_deleted": 1,
                "config_deleted": 1,
            },
        )
        self.assertEqual(self.db.list_memories("bank-a"), [])
        self.assertEqual(self.db.get_observations("bank-a"), [])
        self.assertIsNone(self.db.get_mental_model(model_id))
        self.assertEqual(self.db.get_bank_config("bank-a")["retain_mission"], "")

    def test_count_unconsolidated_memories_is_bank_scoped(self) -> None:
        self.assertEqual(self.db.count_unconsolidated_memories("bank-c"), 0)

        m1 = self.db.insert_memory("bank-c", "one", embedding=[1.0, 0.0])
        self.db.insert_memory("bank-c", "two", embedding=[1.0, 0.0])
        self.assertEqual(self.db.count_unconsolidated_memories("bank-c"), 2)

        # Consolidating one drops the pending count.
        self.db.mark_memories_consolidated([m1])
        self.assertEqual(self.db.count_unconsolidated_memories("bank-c"), 1)

        # A different bank does not affect the count.
        self.db.insert_memory("bank-d", "other", embedding=[1.0, 0.0])
        self.assertEqual(self.db.count_unconsolidated_memories("bank-c"), 1)

    def test_get_embeddings_in_range_filters_by_timestamp(self) -> None:
        a = self.db.insert_memory(
            "bank-t", "early", embedding=[1.0, 0.0],
            timestamp="2026-01-01T00:00:00+00:00",
        )
        b = self.db.insert_memory(
            "bank-t", "mid", embedding=[1.0, 0.0],
            timestamp="2026-06-01T00:00:00+00:00",
        )
        c = self.db.insert_memory(
            "bank-t", "late", embedding=[1.0, 0.0],
            timestamp="2026-12-01T00:00:00+00:00",
        )

        def ids(rows):
            return {r["id"] for r in rows}

        # time_min only — inclusive of the boundary.
        self.assertEqual(
            ids(self.db.get_embeddings_in_range("bank-t", time_min="2026-06-01T00:00:00+00:00")),
            {b, c},
        )
        # time_max only — inclusive of the boundary.
        self.assertEqual(
            ids(self.db.get_embeddings_in_range("bank-t", time_max="2026-06-01T00:00:00+00:00")),
            {a, b},
        )
        # Both bounds.
        self.assertEqual(
            ids(self.db.get_embeddings_in_range(
                "bank-t",
                time_min="2026-02-01T00:00:00+00:00",
                time_max="2026-07-01T00:00:00+00:00",
            )),
            {b},
        )
        # Both bounds on the same boundary value → that single row (both ends inclusive).
        self.assertEqual(
            ids(self.db.get_embeddings_in_range(
                "bank-t",
                time_min="2026-06-01T00:00:00+00:00",
                time_max="2026-06-01T00:00:00+00:00",
            )),
            {b},
        )
        # Out of range → empty.
        self.assertEqual(
            ids(self.db.get_embeddings_in_range("bank-t", time_min="2027-01-01T00:00:00+00:00")),
            set(),
        )
        # No bounds → same id set as get_all_embeddings (backward-compat anchor).
        self.assertEqual(
            ids(self.db.get_embeddings_in_range("bank-t")),
            ids(self.db.get_all_embeddings("bank-t")),
        )

    def test_get_stats_counts_are_count_based(self) -> None:
        m1 = self.db.insert_memory("bank-s", "one", embedding=[1.0, 0.0])
        self.db.insert_memory("bank-s", "two", embedding=[1.0, 0.0])
        self.db.insert_observation("bank-s", "obs", [m1], embedding=[1.0, 0.0])
        self.db.insert_mental_model("bank-s", "topic", "insight", [m1])
        self.db.mark_memories_consolidated([m1])

        stats = self.db.get_stats("bank-s")

        self.assertEqual(stats["memory_count"], 2)
        self.assertEqual(stats["observations_count"], 1)
        self.assertEqual(stats["mental_model_count"], 1)
        # Only the unconsolidated memory (m2) is pending after m1 is consolidated.
        self.assertEqual(stats["unconsolidated_count"], 1)

    def test_mental_model_options_are_persisted(self) -> None:
        model_id = self.db.insert_mental_model_with_options(
            bank_id="bank-a",
            topic="Release Process",
            insight="The team prefers staged rollouts.",
            based_on=["memory-1", "memory-2"],
            auto_refresh=True,
            source_query="release process",
        )

        model = self.db.get_mental_model(model_id)
        self.assertIsNotNone(model)
        self.assertEqual(model["based_on"], ["memory-1", "memory-2"])
        self.assertTrue(model["auto_refresh"])
        self.assertEqual(model["source_query"], "release process")

        auto_models = self.db.get_auto_refresh_models("bank-a")
        self.assertEqual([m["id"] for m in auto_models], [model_id])


if __name__ == "__main__":
    unittest.main()
