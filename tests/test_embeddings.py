import unittest

from kirok_mcp.embeddings import (
    EmbeddingClient,
    TASK_TYPE_DOCUMENT,
    TASK_TYPE_QUERY,
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

    def test_semantic_search_empty_candidates(self) -> None:
        self.assertEqual(semantic_search([1.0, 0.0], [], top_k=5), [])

    def test_semantic_search_excludes_dimension_mismatch(self) -> None:
        candidates = [
            {"id": "ok", "embedding": [1.0, 0.0]},
            {"id": "wrong", "embedding": [1.0, 0.0, 0.0]},
        ]

        results = semantic_search([1.0, 0.0], candidates, top_k=5)

        self.assertEqual([r["id"] for r in results], ["ok"])

    def test_semantic_search_zero_vector_scores_zero(self) -> None:
        results = semantic_search([1.0, 0.0], [{"id": "z", "embedding": [0.0, 0.0]}])
        self.assertEqual(results[0]["similarity"], 0.0)

    def test_semantic_search_matches_cosine_similarity(self) -> None:
        # The bulk numpy path must agree numerically with the per-pair helper.
        candidates = [
            {"id": "a", "embedding": [0.3, 0.7]},
            {"id": "b", "embedding": [0.9, 0.1]},
        ]

        results = semantic_search([0.5, 0.5], candidates, top_k=5)

        by_id = {r["id"]: r["similarity"] for r in results}
        for c in candidates:
            self.assertAlmostEqual(
                by_id[c["id"]],
                cosine_similarity([0.5, 0.5], c["embedding"]),
                places=5,
            )

    def test_semantic_search_preserves_input_order_for_ties(self) -> None:
        # Equal-similarity items keep input order (stable sort), matching the
        # old per-item loop and keeping recall/RRF ranks deterministic.
        candidates = [
            {"id": "first", "embedding": [1.0, 0.0]},
            {"id": "second", "embedding": [1.0, 0.0]},
            {"id": "third", "embedding": [1.0, 0.0]},
        ]

        results = semantic_search([1.0, 0.0], candidates, top_k=3)

        self.assertEqual([r["id"] for r in results], ["first", "second", "third"])


class _FakeEmbedResult:
    def __init__(self, values):
        self.embeddings = [type("E", (), {"values": values})()]


class _FakeAioModels:
    def __init__(self):
        self.calls = []

    async def embed_content(self, model, contents, config):
        self.calls.append({"contents": contents, "task_type": config.task_type})
        return _FakeEmbedResult([0.1, 0.2, 0.3])


class _FakeClient:
    def __init__(self):
        self.aio = type("Aio", (), {"models": _FakeAioModels()})()


class EmbedTaskTypeTest(unittest.IsolatedAsyncioTestCase):
    def _client(self):
        ec = EmbeddingClient(api_key="dummy")
        ec.client = _FakeClient()
        return ec

    async def test_embed_defaults_to_document_and_returns_values(self) -> None:
        ec = self._client()
        vals = await ec.embed("hello")
        self.assertEqual(vals, [0.1, 0.2, 0.3])
        self.assertEqual(ec.client.aio.models.calls[-1]["task_type"], TASK_TYPE_DOCUMENT)

    async def test_embed_passes_query_task_type(self) -> None:
        ec = self._client()
        await ec.embed("q", task_type=TASK_TYPE_QUERY)
        self.assertEqual(ec.client.aio.models.calls[-1]["task_type"], TASK_TYPE_QUERY)

    async def test_embed_batch_defaults_to_document(self) -> None:
        ec = self._client()
        await ec.embed_batch(["a", "b"])
        self.assertEqual(ec.client.aio.models.calls[-1]["task_type"], TASK_TYPE_DOCUMENT)


if __name__ == "__main__":
    unittest.main()
