"""Search paths for MemoryDB: FTS5 keyword search and vector KNN
(with brute-force fallback) over memories and observations."""

import json
import logging
import sqlite3
from typing import Any

from kirok_mcp.db.base import (
    _decode_json_list,
    _deserialize_vector,
    _sanitize_fts_query,
    _serialize_vector,
    _short_cjk_fts_tokens,
)
from kirok_mcp.embeddings import EMBEDDING_DIM, semantic_search


logger = logging.getLogger("kirok.db")


class SearchMixin:
    """Search methods (expects self.conn, self._vec_available)."""

    def fts_search(
        self, bank_id: str, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Full-text search using FTS5 BM25 ranking.

        The query is sanitized to prevent FTS5 parse errors from special
        characters (hyphens interpreted as NOT, uppercase words as column
        names, etc.). If sanitization leaves no valid tokens, or if the
        FTS5 query still fails, returns an empty list gracefully —
        semantic search will still provide results via RRF.

        Short kanji/katakana tokens (1-2 chars) can never MATCH the trigram
        index, so they are served by an exact-substring LIKE supplement
        instead: those rows are appended after the BM25-ranked FTS hits (and
        so rank below them in RRF), newest first.
        """
        assert self.conn is not None

        results: list[dict[str, Any]] = []

        safe_query = _sanitize_fts_query(query)
        if safe_query is not None:
            try:
                rows = self.conn.execute(
                    """SELECT fts.id, fts.content, bm25(fts_memories) AS score,
                              m.timestamp, m.entities
                       FROM fts_memories fts
                       JOIN memories m ON m.id = fts.id
                       WHERE fts_memories MATCH ? AND fts.bank_id = ?
                       ORDER BY score
                       LIMIT ?""",
                    (safe_query, bank_id, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS5 parse error — treat as no FTS hits (the LIKE supplement
                # below and semantic search still work)
                rows = []
            # timestamp/entities let FTS-only hits render with real metadata in
            # recall and be time-filtered directly (entities decoded like the
            # memories loaders — the source column stores them as JSON).
            results = [
                {
                    "id": r["id"],
                    "content": r["content"],
                    "score": r["score"],
                    "timestamp": r["timestamp"],
                    "entities": _decode_json_list(r["entities"]),
                }
                for r in rows
            ]

        # Run the short-token LIKE rescue regardless of how many FTS hits we
        # already have: when FTS fills `limit` with 3+ char matches, a genuine
        # 1-2 char kanji/katakana hit would otherwise be silently dropped. Merge
        # by id, appending after the BM25-ranked FTS hits (rescue rows carry
        # score 0.0 so they naturally rank below them in RRF).
        short_tokens = _short_cjk_fts_tokens(query)
        if short_tokens:
            seen = {r["id"] for r in results}
            for row in self._short_token_like_search(bank_id, short_tokens, limit):
                if row["id"] not in seen:
                    results.append(row)
                    seen.add(row["id"])

        return results

    def _short_token_like_search(
        self, bank_id: str, tokens: list[str], limit: int
    ) -> list[dict[str, Any]]:
        """Exact substring search for tokens the trigram index cannot serve.

        A row must contain ALL tokens (mirroring FTS5's implicit AND across
        quoted tokens) in its content, entities, or keywords. Matching runs
        against fts_memories rather than memories because the source table
        stores entities/keywords as ASCII-escaped JSON (json.dumps default),
        where a CJK substring can never match; the FTS table holds the same
        text space-joined in plain form and is kept in sync by every write
        path. No BM25 score is available, so rows are ordered newest first and
        returned with score 0.0 (BM25 scores are negative = better, so these
        naturally sort last).
        """
        assert self.conn is not None

        conditions = ["m.bank_id = ?"]
        params: list[Any] = [bank_id]
        for token in tokens:
            pattern = "%" + (
                token.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            ) + "%"
            conditions.append(
                r"(f.content LIKE ? ESCAPE '\' "
                r"OR f.entities LIKE ? ESCAPE '\' "
                r"OR f.keywords LIKE ? ESCAPE '\')"
            )
            params.extend([pattern, pattern, pattern])
        params.append(limit)

        rows = self.conn.execute(
            f"""SELECT m.id, m.content, m.timestamp, m.entities
                FROM memories m
                JOIN fts_memories f ON f.id = m.id
                WHERE {' AND '.join(conditions)}
                ORDER BY m.timestamp DESC
                LIMIT ?""",
            params,
        ).fetchall()

        return [
            {
                "id": r["id"],
                "content": r["content"],
                "score": 0.0,
                "timestamp": r["timestamp"],
                "entities": _decode_json_list(r["entities"]),
            }
            for r in rows
        ]

    def get_all_embeddings(self, bank_id: str) -> list[dict[str, Any]]:
        """Load all embeddings for a bank (for brute-force cosine similarity)."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT id, content, embedding, timestamp, context, entities, keywords
               FROM memories
               WHERE bank_id = ? AND embedding IS NOT NULL""",
            (bank_id,),
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "content": r["content"],
                "embedding": _deserialize_vector(r["embedding"]),
                "timestamp": r["timestamp"],
                "context": r["context"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
            })
        return results

    def get_embeddings_in_range(
        self,
        bank_id: str,
        time_min: str | None = None,
        time_max: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load embeddings for a bank, optionally restricted to a timestamp range.

        Same row shape as ``get_all_embeddings``, but pushes the time-window
        filter into SQL so time-filtered recall (which must stay on brute force
        because vec0 cannot filter by timestamp) loads only the candidates it
        needs instead of the whole bank. Bounds are inclusive (>= / <=), matching
        the previous in-Python filter and ``search_by_timestamp``.
        """
        assert self.conn is not None

        conditions = ["bank_id = ?", "embedding IS NOT NULL"]
        params: list[Any] = [bank_id]
        if time_min:
            conditions.append("timestamp >= ?")
            params.append(time_min)
        if time_max:
            conditions.append("timestamp <= ?")
            params.append(time_max)
        where = " AND ".join(conditions)

        rows = self.conn.execute(
            f"""SELECT id, content, embedding, timestamp, context, entities, keywords
               FROM memories
               WHERE {where}""",
            params,
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "content": r["content"],
                "embedding": _deserialize_vector(r["embedding"]),
                "timestamp": r["timestamp"],
                "context": r["context"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
            })
        return results

    def _brute_force_search(
        self, query_embedding: list[float], bank_id: str, top_k: int
    ) -> list[dict[str, Any]]:
        """Brute-force cosine search over all embeddings in a bank.

        This is the fallback path for ``vec_search`` and is also called directly
        by tests. Returns dicts identical to ``get_all_embeddings`` entries plus
        a ``similarity`` score.
        """
        candidates = self.get_all_embeddings(bank_id)
        return semantic_search(query_embedding, candidates, top_k=top_k)

    def vec_search(
        self,
        query_embedding: list[float],
        bank_id: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Per-bank KNN vector search via sqlite-vec.

        ``bank_id`` is a vec0 partition key, so the KNN itself is scoped to the
        bank (``WHERE bank_id = ? AND embedding MATCH ?``), returning exactly the
        bank's nearest neighbours — true parity with the per-bank brute-force
        path, with no cross-bank crowd-out.

        Falls back to brute-force cosine when: the extension is unavailable, the
        KNN errors, or the vec table returns fewer hits than the bank actually
        holds (a sign vec_memories is out of sync). Each result mirrors
        ``get_all_embeddings`` entries plus a cosine ``similarity``
        (``1 - distance``, clamped >= 0) so RRF and the dedup threshold keep
        working unchanged.
        """
        assert self.conn is not None

        if not self._vec_available:
            return self._brute_force_search(query_embedding, bank_id, top_k)

        try:
            query_blob = _serialize_vector(query_embedding)
            knn_rows = self.conn.execute(
                """SELECT memory_id, distance
                   FROM vec_memories
                   WHERE bank_id = ? AND embedding MATCH ?
                   ORDER BY distance
                   LIMIT ?""",
                (bank_id, query_blob, max(top_k, 1)),
            ).fetchall()
        except Exception as e:
            logger.warning(
                "vec_search failed, falling back to brute-force search: %s", e
            )
            return self._brute_force_search(query_embedding, bank_id, top_k)

        # Safety net: if the vec table returned fewer hits than the bank actually
        # holds, vec_memories is out of sync (e.g. an out-of-band edit). Fall back
        # to the authoritative brute-force path rather than silently under-return.
        if len(knn_rows) < top_k:
            bank_count = self.conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE bank_id = ? AND embedding IS NOT NULL "
                "AND length(embedding) = ?",
                (bank_id, EMBEDDING_DIM * 4),
            ).fetchone()[0]
            if len(knn_rows) < min(top_k, bank_count):
                logger.warning(
                    "vec_memories under-returned for bank %s (%s of %s); "
                    "falling back to brute-force search",
                    bank_id,
                    len(knn_rows),
                    bank_count,
                )
                return self._brute_force_search(query_embedding, bank_id, top_k)

        if not knn_rows:
            return []

        distances = {r["memory_id"]: r["distance"] for r in knn_rows}
        ids = list(distances.keys())
        placeholders = ",".join("?" for _ in ids)
        # ids come from this bank's vec partition, so the join stays bank-scoped.
        mem_rows = self.conn.execute(
            f"""SELECT id, content, embedding, timestamp,
                       context, entities, keywords
                FROM memories
                WHERE id IN ({placeholders})""",
            ids,
        ).fetchall()

        results = []
        for r in mem_rows:
            similarity = max(0.0, 1.0 - float(distances[r["id"]]))
            results.append({
                "id": r["id"],
                "content": r["content"],
                "embedding": (
                    _deserialize_vector(r["embedding"]) if r["embedding"] else []
                ),
                "timestamp": r["timestamp"],
                "context": r["context"],
                "entities": json.loads(r["entities"]),
                "keywords": json.loads(r["keywords"]),
                "similarity": similarity,
            })

        # KNN distance order == similarity desc; re-sort after the metadata join.
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    def get_observation_embeddings(
        self, bank_id: str
    ) -> list[dict[str, Any]]:
        """Load all observation embeddings for semantic search."""
        assert self.conn is not None

        rows = self.conn.execute(
            """SELECT id, content, embedding, updated_at, source_memory_ids
               FROM observations
               WHERE bank_id = ? AND embedding IS NOT NULL
                 AND deprecated_at IS NULL""",
            (bank_id,),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "content": r["content"],
                "embedding": _deserialize_vector(r["embedding"]),
                "timestamp": r["updated_at"],
                "source_memory_ids": json.loads(r["source_memory_ids"]),
            }
            for r in rows
        ]

    def _brute_force_search_observations(
        self, query_embedding: list[float], bank_id: str, top_k: int
    ) -> list[dict[str, Any]]:
        """Brute-force cosine search over a bank's observation embeddings."""
        candidates = self.get_observation_embeddings(bank_id)
        return semantic_search(query_embedding, candidates, top_k=top_k)

    def vec_search_observations(
        self, query_embedding: list[float], bank_id: str, top_k: int
    ) -> list[dict[str, Any]]:
        """Per-bank KNN search over observations via sqlite-vec.

        Mirrors ``vec_search`` for the observation layer. Result dicts match the
        ``get_observation_embeddings`` + ``semantic_search`` shape
        (id/content/timestamp/source_memory_ids/similarity) so recall display is
        unchanged. Falls back to brute force when the extension is unavailable,
        the KNN errors, or it under-returns relative to the bank's vec-eligible
        observation count.
        """
        assert self.conn is not None

        if not self._vec_available:
            return self._brute_force_search_observations(query_embedding, bank_id, top_k)

        try:
            query_blob = _serialize_vector(query_embedding)
            knn_rows = self.conn.execute(
                """SELECT observation_id, distance
                   FROM vec_observations
                   WHERE bank_id = ? AND embedding MATCH ?
                   ORDER BY distance
                   LIMIT ?""",
                (bank_id, query_blob, max(top_k, 1)),
            ).fetchall()
        except Exception as e:
            logger.warning(
                "vec_search_observations failed, falling back to brute-force: %s", e
            )
            return self._brute_force_search_observations(query_embedding, bank_id, top_k)

        if len(knn_rows) < top_k:
            bank_count = self.conn.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE bank_id = ? AND embedding IS NOT NULL "
                "AND length(embedding) = ? AND deprecated_at IS NULL",
                (bank_id, EMBEDDING_DIM * 4),
            ).fetchone()[0]
            if len(knn_rows) < min(top_k, bank_count):
                return self._brute_force_search_observations(
                    query_embedding, bank_id, top_k
                )

        if not knn_rows:
            return []

        distances = {r["observation_id"]: r["distance"] for r in knn_rows}
        ids = list(distances.keys())
        placeholders = ",".join("?" for _ in ids)
        obs_rows = self.conn.execute(
            f"""SELECT id, content, updated_at, source_memory_ids
                FROM observations
                WHERE id IN ({placeholders})""",
            ids,
        ).fetchall()

        results = []
        for r in obs_rows:
            similarity = max(0.0, 1.0 - float(distances[r["id"]]))
            results.append({
                "id": r["id"],
                "content": r["content"],
                "timestamp": r["updated_at"],
                "source_memory_ids": json.loads(r["source_memory_ids"]),
                "similarity": similarity,
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]
