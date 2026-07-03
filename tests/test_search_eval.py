"""Offline tests for scripts/search_eval.py metric logic and case runner.

The script itself needs a real DB + API key; the metric functions and the
case runner are pure/fake-friendly, so their behavior is pinned here.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "search_eval.py"
_spec = importlib.util.spec_from_file_location("search_eval", _SCRIPT)
search_eval = importlib.util.module_from_spec(_spec)
sys.modules["search_eval"] = search_eval
_spec.loader.exec_module(search_eval)


class FirstHitRankTest(unittest.TestCase):
    RESULTS = [
        {"id": "a", "content": "uv sync fails on Windows"},
        {"id": "b", "content": "recycle.ps1 sends files to the bin"},
        {"id": "c", "content": "京都で紅葉を見た"},
    ]

    def test_id_match_returns_rank(self) -> None:
        self.assertEqual(
            search_eval.first_hit_rank(self.RESULTS, ["b"], []), 2
        )

    def test_substring_match_returns_rank(self) -> None:
        self.assertEqual(
            search_eval.first_hit_rank(self.RESULTS, [], ["紅葉"]), 3
        )

    def test_id_and_substring_take_earliest_rank(self) -> None:
        self.assertEqual(
            search_eval.first_hit_rank(self.RESULTS, ["c"], ["uv sync"]), 1
        )

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(
            search_eval.first_hit_rank(self.RESULTS, ["z"], ["missing"])
        )

    def test_empty_results_returns_none(self) -> None:
        self.assertIsNone(search_eval.first_hit_rank([], ["a"], ["x"]))


class SummarizeTest(unittest.TestCase):
    def test_hit_rates_and_mrr(self) -> None:
        # ranks: hit at 1, hit at 3, miss, hit at 7
        summary = search_eval.summarize([1, 3, None, 7], ks=(1, 5, 10))
        self.assertEqual(summary["cases"], 4)
        self.assertAlmostEqual(summary["hit@1"], 1 / 4)
        self.assertAlmostEqual(summary["hit@5"], 2 / 4)
        self.assertAlmostEqual(summary["hit@10"], 3 / 4)
        # MRR = (1/1 + 1/3 + 0 + 1/7) / 4
        self.assertAlmostEqual(summary["mrr"], (1 + 1 / 3 + 1 / 7) / 4)

    def test_empty_ranks(self) -> None:
        summary = search_eval.summarize([], ks=(1, 5))
        self.assertEqual(summary["cases"], 0)
        self.assertEqual(summary["mrr"], 0.0)
        self.assertEqual(summary["hit@1"], 0.0)


class _FakeDB:
    """Minimal DB whose search results are fixed per bank."""

    def vec_search(self, query_embedding, bank_id, top_k, **kwargs):
        return [
            {
                "id": "mem-1",
                "content": "uv sync fails on Windows cloud-sync folders",
                "embedding": [1.0, 0.0],
                "timestamp": "2026-06-01T00:00:00+00:00",
                "context": "",
                "entities": [],
                "keywords": [],
                "similarity": 0.9,
            }
        ][:top_k]

    def fts_search(self, bank_id, query, limit=20):
        return [{"id": "mem-1", "content": "uv sync fails on Windows", "score": -1.0}]


class _FakeEmbedder:
    async def embed(self, text, task_type=""):
        return [1.0, 0.0]


class RunCasesTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_cases_uses_real_recall_pipeline(self) -> None:
        cases = [
            {
                "bank_id": "work-history",
                "query": "uv sync problem",
                "expected_substrings": ["uv sync"],
            },
            {
                "bank_id": "work-history",
                "query": "unrelated",
                "expected_ids": ["ghost"],
            },
        ]

        outcomes = await search_eval.run_cases(_FakeDB(), _FakeEmbedder(), cases, limit=10)

        self.assertEqual(outcomes[0]["rank"], 1)
        self.assertIsNone(outcomes[1]["rank"])
        self.assertEqual(outcomes[1]["top_id"], "mem-1")


if __name__ == "__main__":
    unittest.main()
