import unittest

from kirok_mcp.embeddings import (
    cosine_similarity,
    reciprocal_rank_fusion,
    semantic_search,
)


class EmbeddingsTest(unittest.TestCase):
    def test_cosine_similarity(self) -> None:
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 0.0]), 0.0)

    def test_semantic_search_orders_by_similarity(self) -> None:
        candidates = [
            {"id": "low", "embedding": [0.0, 1.0], "content": "low"},
            {"id": "high", "embedding": [1.0, 0.0], "content": "high"},
            {"id": "mid", "embedding": [0.5, 0.5], "content": "mid"},
        ]

        results = semantic_search([1.0, 0.0], candidates, top_k=2)

        self.assertEqual([r["id"] for r in results], ["high", "mid"])
        self.assertGreater(results[0]["similarity"], results[1]["similarity"])

    def test_reciprocal_rank_fusion_merges_duplicate_results(self) -> None:
        semantic_results = [
            {"id": "a", "content": "semantic a"},
            {"id": "b", "content": "semantic b"},
        ]
        keyword_results = [
            {"id": "b", "content": "keyword b with richer metadata", "score": 0.1},
            {"id": "c", "content": "keyword c", "score": 0.2},
        ]

        results = reciprocal_rank_fusion(semantic_results, keyword_results, k=60)

        self.assertEqual([r["id"] for r in results], ["b", "a", "c"])
        self.assertIn("richer metadata", results[0]["content"])
        self.assertGreater(results[0]["rrf_score"], results[1]["rrf_score"])


if __name__ == "__main__":
    unittest.main()
