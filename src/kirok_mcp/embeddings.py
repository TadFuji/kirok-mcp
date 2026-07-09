"""Gemini Embedding API wrapper for Kirok memory vectors.

Uses gemini-embedding-001 model for text embedding generation
and provides cosine similarity utilities for vector search.
"""

import logging

import numpy as np
from google import genai
from google.genai import types

from kirok_mcp.retry import with_retry


logger = logging.getLogger("kirok.embeddings")

# Embedding model — GA as of July 2025, 2048 max tokens, 100+ languages
EMBEDDING_MODEL = "gemini-embedding-001"

# Output dimension of gemini-embedding-001 when output_dimensionality is left
# unset (the model's full-fidelity default). This MUST match both the vec0
# schema `float[EMBEDDING_DIM]` and the float32 BLOBs already stored in
# memory.db (12288 bytes = 3072 float32). Do not change without re-embedding.
# At 3072 the model returns pre-normalized vectors, so no manual L2 step is
# needed (a smaller output_dimensionality would require one).
EMBEDDING_DIM = 3072

# Asymmetric retrieval task types. Stored documents (memories, observations) use
# RETRIEVAL_DOCUMENT; search queries use RETRIEVAL_QUERY. The pairing improves
# retrieval relevance over symmetric (untyped) embeddings.
TASK_TYPE_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_TYPE_QUERY = "RETRIEVAL_QUERY"


class EmbeddingClient:
    """Wrapper for Gemini Embedding API with similarity utilities."""

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        # ponytail: session-lifetime counter, resets on restart. Upgrade path:
        # persist to a table if long-term API-usage accounting is ever needed.
        self.api_calls = 0

    async def embed(
        self, text: str, task_type: str = TASK_TYPE_DOCUMENT
    ) -> list[float]:
        """Generate embedding vector for a single text input.

        Uses the SDK's async client (``client.aio``) so the call yields to the
        event loop instead of blocking it — this is what lets concurrent MCP
        requests overlap and lets ``asyncio.wait_for`` timeouts actually fire.

        ``task_type`` defaults to RETRIEVAL_DOCUMENT (the safe, storage side);
        callers searching pass RETRIEVAL_QUERY.
        """
        result = await with_retry(
            lambda: self.client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(task_type=task_type),
            ),
            logger=logger,
        )
        self.api_calls += 1
        values = list(result.embeddings[0].values)
        # Guard against silent dimension drift (model swap, config change): a
        # wrong-width vector would be stored but excluded from vec_search and the
        # brute-force path, vanishing from semantic recall without any warning.
        if len(values) != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding API returned {len(values)}-dim vector, "
                f"expected {EMBEDDING_DIM} (model/config drift?)."
            )
        return values

    async def embed_batch(
        self, texts: list[str], task_type: str = TASK_TYPE_DOCUMENT
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts in one call."""
        if not texts:
            return []

        result = await with_retry(
            lambda: self.client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(task_type=task_type),
            ),
            logger=logger,
        )
        self.api_calls += 1
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
    if not candidates:
        return []

    q = np.asarray(query_embedding, dtype=np.float32)
    qd = q.shape[0]

    # Keep only candidates whose stored vector width matches the query, then
    # stack into one (N, D) matrix. Mismatched lengths would make np.array build
    # an object array and break the matmul; this also keeps the brute-force path
    # aligned with vec_search, which excludes off-size vectors from the index.
    kept = [c for c in candidates if len(c["embedding"]) == qd]
    if not kept:
        return []

    mat = np.array([c["embedding"] for c in kept], dtype=np.float32)  # (N, D)
    q_norm = np.linalg.norm(q)
    row_norms = np.linalg.norm(mat, axis=1)
    denom = row_norms * q_norm
    dots = mat @ q
    # Match cosine_similarity: a zero-norm vector (query or candidate) scores 0.0.
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = np.where(denom > 0, dots / denom, 0.0)

    scored = [{**item, "similarity": float(s)} for item, s in zip(kept, sims)]
    # Stable sort preserves input order for equal scores, matching the previous
    # per-item loop. np.argsort is unstable and would reorder ties, perturbing
    # recall output order and downstream RRF ranks.
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
            # Merge field-by-field so the same id seen in multiple lists keeps
            # every key: an FTS-only hit gains semantic metadata and vice versa.
            # First writer wins on shared keys (deterministic, independent of
            # which list happened to carry a bulky field like `embedding`).
            if item_id not in items:
                items[item_id] = dict(item)
            else:
                merged = items[item_id]
                for key, value in item.items():
                    if key not in merged:
                        merged[key] = value

    # Build result with RRF scores
    result = []
    for item_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        result.append({**items[item_id], "rrf_score": score})

    return result
