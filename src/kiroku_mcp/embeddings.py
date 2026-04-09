"""Gemini Embedding API wrapper for Kiroku memory vectors.

Uses gemini-embedding-001 model for text embedding generation
and provides cosine similarity utilities for vector search.
"""

import numpy as np
from google import genai


# Embedding model — GA as of July 2025, 2048 max tokens, 100+ languages
EMBEDDING_MODEL = "gemini-embedding-001"


class EmbeddingClient:
    """Wrapper for Gemini Embedding API with similarity utilities."""

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    async def embed(self, text: str) -> list[float]:
        """Generate embedding vector for a single text input."""
        result = self.client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
        )
        return list(result.embeddings[0].values)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in one call."""
        if not texts:
            return []

        result = self.client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
        )
        return [list(e.values) for e in result.embeddings]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(dot / norm)


def semantic_search(
    query_embedding: list[float],
    candidates: list[dict],
    top_k: int = 10,
) -> list[dict]:
    """Rank candidates by cosine similarity to query embedding.

    Args:
        query_embedding: The query vector.
        candidates: List of dicts, each must have an 'embedding' key.
        top_k: Number of top results to return.

    Returns:
        Top-k candidates with added 'similarity' score, sorted descending.
    """
    scored = []
    for item in candidates:
        sim = cosine_similarity(query_embedding, item["embedding"])
        scored.append({**item, "similarity": sim})

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def reciprocal_rank_fusion(
    *ranked_lists: list[dict],
    k: int = 60,
    id_key: str = "id",
) -> list[dict]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion (RRF).

    RRF score = sum(1 / (k + rank_i)) for each list where the item appears.
    Higher k gives more weight to items appearing in multiple lists.

    Args:
        *ranked_lists: Variable number of ranked result lists.
        k: RRF constant (default 60, standard value from the RRF paper).
        id_key: Key to use as unique identifier.

    Returns:
        Merged list sorted by RRF score (descending), with duplicates removed.
    """
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            item_id = item[id_key]
            rrf_score = 1.0 / (k + rank + 1)
            scores[item_id] = scores.get(item_id, 0.0) + rrf_score
            # Keep the richest version of the item
            if item_id not in items or len(str(item)) > len(str(items[item_id])):
                items[item_id] = item

    # Build result with RRF scores
    result = []
    for item_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        result.append({**items[item_id], "rrf_score": score})

    return result
