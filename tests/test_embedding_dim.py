"""Embedding dimension matches gemini-embedding-001 default (3072).

gemini-embedding-001 returns 3072-dimensional vectors when
output_dimensionality is not set — the value kirok actually produces and has
already persisted in memory.db. EMBEDDING_DIM is pinned to that so the
sqlite-vec vec0 schema (float[EMBEDDING_DIM]) stays aligned with the stored
embeddings.
"""

from kirok_mcp.embeddings import EMBEDDING_DIM


def test_embedding_dim_is_3072():
    assert EMBEDDING_DIM == 3072
