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
        sanitized = _sanitize_fts_query('2026-03-25 ASCII AND NEAR("x" "y") * ^')

        self.assertEqual(sanitized, '"2026" "03" "25" "ASCII" "x" "y"')
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
