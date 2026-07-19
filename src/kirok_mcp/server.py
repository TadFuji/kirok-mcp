#!/usr/bin/env python3
"""Kirok Memory MCP Server.

An agent memory system with Retain/Recall/Reflect operations,
plus autonomous learning via Observation Consolidation.
Uses SQLite for storage, Gemini Embeddings for semantic search,
and Gemini Flash for entity extraction, reflection, and consolidation.
"""

import asyncio
import atexit
import logging
import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from kirok_mcp.backup import auto_snapshot
from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import (
    EmbeddingClient,
    TASK_TYPE_DOCUMENT,
    TASK_TYPE_QUERY,
    reciprocal_rank_fusion,
    semantic_search,
)
from kirok_mcp.llm import LLMClient

# ── Load environment ──────────────────────────────────────────────────

_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.dirname(os.path.dirname(_pkg_dir))
load_dotenv(os.path.join(_project_dir, ".env"))

DB_PATH = os.environ.get("KIROK_DB_PATH", None)
REFLECT_TIMEOUT = int(os.environ.get("KIROK_REFLECT_TIMEOUT", "300"))
CONSOLIDATION_TIMEOUT = int(os.environ.get("KIROK_CONSOLIDATION_TIMEOUT", "120"))
# Auto-consolidation is debounced: rather than running after every retain (a
# large LLM call plus embeddings), it runs only once at least this many memories
# are pending. Set to 1 to restore the old "consolidate on every retain".
CONSOLIDATION_BATCH_SIZE = int(os.environ.get("KIROK_CONSOLIDATION_BATCH_SIZE", "5"))
# Startup auto-snapshot: safety net for users who never run the manual
# `kirok-backup snapshot` CLI. 0 disables it.
AUTO_SNAPSHOT_HOURS = float(os.environ.get("KIROK_AUTO_SNAPSHOT_HOURS", "24"))
SNAPSHOT_KEEP = int(os.environ.get("KIROK_SNAPSHOT_KEEP", "5"))

# ── Logging ───────────────────────────────────────────────────────────

logger = logging.getLogger("kirok.server")

# ── Runtime clients ───────────────────────────────────────────────────
# Created by _init_runtime() at server startup, NOT at import: importing this
# module (tests, tooling, diagnostics) must not open the database, require an
# API key, or register atexit hooks. Tests swap these for fakes.

_db: MemoryDB | None = None
_embedder: EmbeddingClient | None = None
_llm: LLMClient | None = None

# Per-bank locks serializing consolidation runs. Without them, two retains
# landing close together can both pass the debounce, read the same
# unconsolidated batch, and produce duplicate observations from it.
_consolidation_locks: dict[str, asyncio.Lock] = {}

mcp = FastMCP("kirok_mcp")


def _init_runtime() -> None:
    """Connect the database and create the Gemini clients.

    Exits with an error if GEMINI_API_KEY is missing — checked here rather
    than at import so `import kirok_mcp.server` never kills the process.
    """
    global _db, _embedder, _llm

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print(
            "ERROR: GEMINI_API_KEY environment variable is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    _db = MemoryDB(db_path=DB_PATH)
    _db.connect()
    atexit.register(_db.close)

    # Best-effort startup snapshot (mtime-gated, so a normal restart within
    # KIROK_AUTO_SNAPSHOT_HOURS is just one glob() call). Runs synchronously —
    # VACUUM INTO opens its own sqlite connections and only actually copies the
    # database once a day, so the rare multi-second startup delay is preferred
    # over a background thread touching `_db.conn` from a second thread
    # (sqlite3 connections are not thread-safe by default).
    try:
        auto_snapshot(
            _db.db_path, interval_hours=AUTO_SNAPSHOT_HOURS, keep=SNAPSHOT_KEEP
        )
    except Exception as e:
        logger.warning("Auto-snapshot failed: %s", e)
        _db.record_failure("_system", "auto_snapshot", str(e))

    _embedder = EmbeddingClient(api_key=api_key)
    _llm = LLMClient(api_key=api_key)


# ── Internal: Deduplication Threshold ─────────────────────────────────

DEDUP_SIMILARITY_THRESHOLD = float(
    os.environ.get("KIROK_DEDUP_THRESHOLD", "0.85")
)

# Minimum cosine similarity for a semantic (vector) memory hit to reach recall.
# Without a floor an unrelated query still returns `limit` full memories from
# any non-empty bank (context pollution). FTS keyword hits are exempt — a
# literal term match is independent, strong evidence and is not floored here.
#
# 0.62 is calibrated on live data (2026-07-10): gemini-embedding-001
# similarities cluster in a narrow band — off-topic queries score 0.55-0.62
# against unrelated banks while true hits score 0.66-0.73 — so the usable
# floor sits just above the off-topic ceiling. Golden-set hit@5/MRR are
# unchanged between 0.3 and 0.65.
RECALL_MIN_SIMILARITY = float(
    os.environ.get("KIROK_RECALL_MIN_SIMILARITY", "0.62")
)

# Minimum similarity for a consolidated observation to be shown in recall.
# Same 0.62 calibration as RECALL_MIN_SIMILARITY (the historical 0.4 was below
# even off-topic scores, so it never filtered anything).
OBS_MIN_SIMILARITY = float(
    os.environ.get("KIROK_OBS_MIN_SIMILARITY", "0.62")
)


# ── Internal: Consolidation Engine ────────────────────────────────────

async def _run_consolidation(bank_id: str) -> str:
    """Run observation consolidation for a bank.

    Finds unconsolidated memories, compares against existing observations,
    and creates/updates/deletes observations as needed. Also auto-refreshes
    mental models with auto_refresh=True.

    Serialized per bank: a second concurrent run waits, then re-reads the
    (now empty) unconsolidated set and exits — instead of double-processing
    the same batch into duplicate observations. A timeout cancellation from
    ``asyncio.wait_for`` releases the lock via ``async with``.

    Returns a summary string of what happened.
    """
    lock = _consolidation_locks.setdefault(bank_id, asyncio.Lock())
    async with lock:
        return await _run_consolidation_locked(bank_id)


async def _run_consolidation_locked(bank_id: str) -> str:
    new_memories = _db.get_unconsolidated_memories(bank_id, limit=50)
    if not new_memories:
        return "No unconsolidated memories found."

    existing_obs = _db.get_observations(bank_id, limit=100)
    config = _db.get_bank_config(bank_id)
    obs_mission = config.get("observations_mission", "")

    # Ask LLM to consolidate
    actions = await _llm.consolidate(
        new_memories=new_memories,
        existing_observations=existing_obs,
        observations_mission=obs_mission,
    )

    created_count = 0
    updated_count = 0
    deleted_count = 0

    # Pre-generate every create/update embedding BEFORE touching the database.
    # If an embed API call fails here we raise without having written anything,
    # so the DB is untouched and the memories stay unconsolidated for a later
    # retry — instead of leaving observations half-applied.
    for action in actions:
        if action["action"] in ("create", "update"):
            action["_embedding"] = await _embedder.embed(
                action["content"], task_type=TASK_TYPE_DOCUMENT
            )

    # Apply all observation changes and the consolidated mark as ONE atomic
    # transaction: every observation write runs with commit=False so nothing is
    # persisted until mark_memories_consolidated commits the whole batch. A
    # failure in any step self-rolls-back and re-raises (see the db methods), so
    # observations can never be partially applied while memories go unmarked.
    for action in actions:
        if action["action"] == "delete" and action.get("observation_id"):
            # DELETE: soft-deprecate the contradicted/obsolete observation
            # (keeps the row + logs an audit event) rather than destroy it.
            if _db.deprecate_observation(
                action["observation_id"],
                reason=action.get("content", "no reason"),
                commit=False,
            ):
                deleted_count += 1
                logger.info(
                    "Observation deprecated: %s (reason: %s)",
                    action["observation_id"],
                    action.get("content", "no reason"),
                )
            continue

        if action["action"] == "create":
            _db.insert_observation(
                bank_id=bank_id,
                content=action["content"],
                source_memory_ids=action["source_memory_ids"],
                embedding=action["_embedding"],
                commit=False,
            )
            created_count += 1
        elif action["action"] == "update" and action.get("observation_id"):
            # Merge source memory IDs with existing ones
            for obs in existing_obs:
                if obs["id"] == action["observation_id"]:
                    merged_ids = list(set(
                        obs.get("source_memory_ids", []) +
                        action["source_memory_ids"]
                    ))
                    _db.update_observation(
                        observation_id=action["observation_id"],
                        content=action["content"],
                        source_memory_ids=merged_ids,
                        embedding=action["_embedding"],
                        commit=False,
                    )
                    updated_count += 1
                    break

    # Commit the batch by marking the source memories consolidated last.
    consolidated_ids = [m["id"] for m in new_memories]
    _db.mark_memories_consolidated(consolidated_ids)

    # Auto-refresh mental models if any observations changed
    refresh_summary = ""
    if created_count > 0 or updated_count > 0 or deleted_count > 0:
        auto_models = _db.get_auto_refresh_models(bank_id)
        refreshed = 0
        for model in auto_models:
            try:
                query = model.get("source_query") or model["topic"]
                query_emb = await _embedder.embed(query, task_type=TASK_TYPE_QUERY)
                relevant = _db.vec_search(query_emb, bank_id, top_k=20)
                if relevant:
                    reflection = await _llm.reflect(
                        query=query,
                        memories=relevant,
                        existing_models=[model],
                    )
                    _db.update_mental_model(
                        model_id=model["id"],
                        topic=reflection["topic"],
                        insight=reflection["insight"],
                        based_on=[m["id"] for m in relevant],
                    )
                    refreshed += 1
            except Exception as e:
                logger.warning("Auto-refresh failed for model %s: %s", model["id"], e)
                _db.record_failure(
                    bank_id,
                    "mental_model_auto_refresh",
                    f"model {model['id']}: {e}",
                )

        if refreshed > 0:
            refresh_summary = f"\n- Mental Models auto-refreshed: {refreshed}"

    return (
        f"Consolidation complete.\n"
        f"- Memories processed: {len(new_memories)}\n"
        f"- Observations created: {created_count}\n"
        f"- Observations updated: {updated_count}\n"
        f"- Observations deleted: {deleted_count}"
        f"{refresh_summary}"
    )


async def _maybe_consolidate(bank_id: str) -> None:
    """Run auto-consolidation, debounced by CONSOLIDATION_BATCH_SIZE.

    Consolidation is comparatively expensive (a large LLM call plus an embedding
    per observation, and possibly mental-model refresh), so we don't run it after
    every single retain. Instead we wait until enough memories are pending. Any
    error is logged and swallowed so a consolidation hiccup never fails the
    retain that triggered it; the leftover memories simply roll into the next
    batch (or a manual ``KIROK_consolidate``).
    """
    try:
        pending = _db.count_unconsolidated_memories(bank_id)
    except Exception as e:
        logger.warning(
            "Could not count unconsolidated memories for '%s': %s", bank_id, e
        )
        # Surface the skip in KIROK_stats like every other background failure;
        # best-effort — if even the event insert fails, stay fail-open.
        try:
            _db.record_failure(bank_id, "consolidation_count", str(e))
        except Exception:
            pass
        return

    if pending < CONSOLIDATION_BATCH_SIZE:
        return

    try:
        await asyncio.wait_for(
            _run_consolidation(bank_id),
            timeout=CONSOLIDATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("Auto-consolidation timed out for bank '%s'", bank_id)
        _db.record_failure(
            bank_id,
            "auto_consolidation_timeout",
            f"timed out after {CONSOLIDATION_TIMEOUT}s",
        )
    except Exception as e:
        logger.warning("Auto-consolidation failed for bank '%s': %s", bank_id, e)
        _db.record_failure(bank_id, "auto_consolidation", str(e))


async def _retain_memory(
    bank_id: str,
    content: str,
    context: str = "",
    timestamp: str = "",
) -> str:
    """Shared retain pipeline used by normal and smart retain."""
    # Get bank config for retain mission
    config = _db.get_bank_config(bank_id)
    mission = config.get("retain_mission", "")

    # DOCUMENT embedding: this vector is stored. It is also reused as the dedup
    # query below (vec_search), which compares two to-be-stored documents — a
    # document-document comparison, so RETRIEVAL_DOCUMENT is the right type and we
    # avoid a second QUERY embedding call.
    #
    # ponytail: embed + extract_entities run concurrently. The dedup NOOP/UPDATE
    # branches discard the ADD extraction, but ADD is the common case so
    # overlapping the two Gemini round-trips is a net latency win.
    embedding, extraction = await asyncio.gather(
        _embedder.embed(content, task_type=TASK_TYPE_DOCUMENT),
        _llm.extract_entities(content, mission=mission),
    )

    # ── Smart Deduplication: check for similar existing memories ──
    similar = _db.vec_search(embedding, bank_id, top_k=5)
    if similar:
        # Filter to only highly similar memories
        highly_similar = [
            m for m in similar
            if m.get("similarity", 0) > DEDUP_SIMILARITY_THRESHOLD
        ]

        if highly_similar:
            dedup_result = await _llm.deduplicate(
                new_content=content,
                similar_memories=highly_similar,
                mission=mission,
            )

            if dedup_result["action"] == "noop":
                return (
                    f"Memory NOT stored (duplicate detected).\n\n"
                    f"- Action: NOOP\n"
                    f"- Reason: {dedup_result['reason']}\n"
                    f"- Similar to: {highly_similar[0]['id']}\n"
                )

            if dedup_result["action"] == "update":
                target_id = dedup_result.get("target_memory_id", "")
                merged = dedup_result.get("merged_content", content)

                # Capture the memory's pre-merge content before the LLM overwrites
                # it, so a bad merge can be reconstructed from the audit log.
                old_content = next(
                    (m["content"] for m in highly_similar if m["id"] == target_id),
                    "",
                )

                # Re-extract entities for merged content (concurrently with the
                # merged embedding — independent Gemini round-trips).
                merged_extraction, merged_emb = await asyncio.gather(
                    _llm.extract_entities(merged, mission=mission),
                    _embedder.embed(merged, task_type=TASK_TYPE_DOCUMENT),
                )

                # The merge and its audit row commit as ONE transaction
                # (commit=False + the event's commit=True): a crash between
                # them could otherwise persist the merge with no audit row,
                # making the pre-merge content unrecoverable — exactly what
                # the audit exists to prevent.
                updated = _db.update_memory(
                    memory_id=target_id,
                    content=merged,
                    entities=merged_extraction["entities"],
                    keywords=merged_extraction["keywords"],
                    context=context or None,
                    embedding=merged_emb,
                    commit=False,
                )

                if updated:
                    # Audit the pre-merge content (excluded from failure surfacing
                    # via _AUDIT_EVENTS) so an LLM merge never silently erases a
                    # memory without a recoverable trace.
                    try:
                        _db._record_event(
                            bank_id,
                            "memory_dedup_update",
                            f"{target_id}: {old_content}",
                            commit=True,
                        )
                    except Exception:
                        _db.conn.rollback()
                        raise

                    result = (
                        f"Memory UPDATED (enriched existing).\n\n"
                        f"- Action: UPDATE\n"
                        f"- Reason: {dedup_result['reason']}\n"
                        f"- Updated ID: {target_id}\n"
                        f"- Entities: {', '.join(merged_extraction['entities']) or '(none)'}\n"
                        f"- Keywords: {', '.join(merged_extraction['keywords']) or '(none)'}\n"
                    )

                    # Debounced background-style consolidation (runs only once
                    # enough memories are pending; never appended to the reply).
                    await _maybe_consolidate(bank_id)
                    return result
                # If update failed (ID not found), fall through to ADD

    # ── Normal ADD flow ──
    # `extraction` was computed up-front alongside the embedding (see gather).
    memory_id = _db.insert_memory(
        bank_id=bank_id,
        content=content,
        embedding=embedding,
        entities=extraction["entities"],
        keywords=extraction["keywords"],
        context=context,
        timestamp=timestamp or None,
    )

    entities_str = ", ".join(extraction["entities"]) if extraction["entities"] else "(none)"
    keywords_str = ", ".join(extraction["keywords"]) if extraction["keywords"] else "(none)"

    result = (
        f"Memory stored successfully.\n\n"
        f"- Action: ADD\n"
        f"- ID: {memory_id}\n"
        f"- Bank: {bank_id}\n"
        f"- Entities: {entities_str}\n"
        f"- Keywords: {keywords_str}\n"
    )

    # Debounced auto-consolidation (runs only once enough memories are pending;
    # kept off the reply to save tokens). Leftovers roll into the next batch or
    # a manual KIROK_consolidate.
    await _maybe_consolidate(bank_id)
    return result


# ── Tool: Retain ──────────────────────────────────────────────────────

@mcp.tool()
async def KIROK_retain(
    bank_id: str,
    content: str,
    context: str = "",
    timestamp: str = "",
) -> str:
    """Store new information in agent memory.

    Automatically extracts entities and keywords, generates a semantic
    embedding, and indexes for later retrieval.

    Smart Deduplication (inspired by Mem0): If the new content is highly
    similar to existing memories (cosine > 0.85), the system will decide
    whether to ADD (new info), UPDATE (enrich existing), or NOOP (skip).

    Args:
        bank_id: Memory bank identifier (e.g. 'antigravity', 'user-prefs').
        content: The information to remember.
        context: Optional context about the source (e.g. 'project meeting').
        timestamp: Optional ISO 8601 timestamp. Defaults to now.
    """
    return await _retain_memory(
        bank_id=bank_id,
        content=content,
        context=context,
        timestamp=timestamp,
    )


# ── Tool: Smart Retain ────────────────────────────────────────────────

@mcp.tool()
async def KIROK_smart_retain(
    bank_id: str,
    content: str,
    context: str = "",
    timestamp: str = "",
    threshold: int = 5,
) -> str:
    """Evaluate content importance before retaining. Uses LLM to score
    the content from 1-10 and only retains if score >= threshold.

    Use this for bulk/automatic ingestion where you want the system
    to decide what's worth remembering.

    Args:
        bank_id: Memory bank identifier.
        content: The information to potentially remember.
        context: Optional context about the source.
        timestamp: Optional ISO 8601 timestamp.
        threshold: Minimum importance score to retain (1-10, default 5).
    """
    config = _db.get_bank_config(bank_id)
    mission = config.get("retain_mission", "")

    evaluation = await _llm.evaluate_importance(
        content, mission=mission, threshold=threshold
    )

    if not evaluation["should_retain"]:
        return (
            f"Content not retained (below threshold).\n\n"
            f"- Score: {evaluation['score']}/10 (threshold: {threshold})\n"
            f"- Reason: {evaluation['reason']}\n"
        )

    # Content is important enough — proceed through the same retain pipeline
    # so deduplication, updates, and auto-consolidation stay consistent.
    retain_result = await _retain_memory(
        bank_id=bank_id,
        content=content,
        context=context,
        timestamp=timestamp,
    )

    return (
        f"Content passed importance filter.\n\n"
        f"- Score: {evaluation['score']}/10 (threshold: {threshold})\n"
        f"- Reason: {evaluation['reason']}\n\n"
        f"{retain_result}"
    )


# ── Tool: Recall ──────────────────────────────────────────────────────

async def hybrid_search_memories(
    db,
    embedder,
    bank_id: str,
    query: str,
    limit: int,
    time_min: str = "",
    time_max: str = "",
    *,
    query_embedding: list[float] | None = None,
) -> list[dict]:
    """Semantic + keyword memory search merged with RRF.

    This is the exact memory-search pipeline KIROK_recall uses, extracted so
    scripts/search_eval.py can measure the real recall path instead of a copy
    that would drift. Takes db/embedder as arguments (not the module globals)
    so callers outside the running server can supply their own clients.
    ``query_embedding`` lets a caller that already embedded the query (recall
    reuses it for observation search) avoid a second API call.
    """
    if query_embedding is None:
        query_embedding = await embedder.embed(query, task_type=TASK_TYPE_QUERY)

    # Each source is fetched deeper than the final page: RRF rewards documents
    # that appear in both lists, so an item ranked just outside `limit` in each
    # source (a classic true hit) must still reach the fusion — truncating the
    # pools at `limit` would drop it before RRF can promote it.
    fetch_depth = max(limit * 3, 30)

    # Time-filtered recall stays on the brute-force path (vec0 cannot filter by
    # timestamp); otherwise use the fast vec_search KNN.
    if time_min or time_max:
        filtered = db.get_embeddings_in_range(
            bank_id, time_min=time_min or None, time_max=time_max or None
        )
        semantic_results = semantic_search(query_embedding, filtered, top_k=fetch_depth)
    else:
        semantic_results = db.vec_search(query_embedding, bank_id, top_k=fetch_depth)

    fts_results = db.fts_search(bank_id, query, limit=fetch_depth)
    # FTS hits carry their timestamp, so the window filters directly. Bounds
    # inclusive, ISO 8601 lexicographic compare — same semantics as
    # get_embeddings_in_range.
    if time_min or time_max:
        fts_results = [
            r
            for r in fts_results
            if (not time_min or r["timestamp"] >= time_min)
            and (not time_max or r["timestamp"] <= time_max)
        ]

    # Floor the semantic side so an unrelated query cannot return full memories
    # purely because the bank is non-empty. FTS hits pass through unfiltered:
    # a literal keyword match is independent evidence, not a weak vector score.
    semantic_results = [
        r
        for r in semantic_results
        if r.get("similarity", 0.0) >= RECALL_MIN_SIMILARITY
    ]

    merged = reciprocal_rank_fusion(semantic_results, fts_results, k=60)
    return merged[:limit]


@mcp.tool()
async def KIROK_recall(
    bank_id: str,
    query: str,
    limit: int = 10,
    time_min: str = "",
    time_max: str = "",
    verbose: bool = False,
) -> str:
    """Search and retrieve relevant memories using semantic similarity
    and keyword matching, merged with Reciprocal Rank Fusion.

    Args:
        bank_id: Memory bank to search.
        query: Natural language search query.
        limit: Maximum number of results (default 10, max 50).
        time_min: Optional ISO 8601 lower bound for timestamp filtering.
        time_max: Optional ISO 8601 upper bound for timestamp filtering.
        verbose: If True, also show relevance scores (RRF/Sim) per item.
            Default False keeps the output compact (content + ID only) to
            save context tokens.
    """
    limit = min(max(limit, 1), 50)

    # Reject empty / whitespace-only queries early
    if not query or not query.strip():
        return "Error: query must not be empty. Please provide a search term."

    query_embedding = await _embedder.embed(query, task_type=TASK_TYPE_QUERY)
    top_results = await hybrid_search_memories(
        _db,
        _embedder,
        bank_id,
        query,
        limit,
        time_min=time_min,
        time_max=time_max,
        query_embedding=query_embedding,
    )

    # ── Observation-first display (inspired by Mem0 knowledge layer) ──
    # Hybrid, mirroring the memory side: semantic hits are floored by
    # OBS_MIN_SIMILARITY, while keyword hits from fts_observations pass
    # unfloored (a literal term match is independent evidence). Both fuse via
    # RRF. Without the keyword path, an observation whose cosine lands just
    # under the floor was unreachable even on an exact keyword match — the
    # fts_observations index was maintained but never queried.
    obs_sem = _db.vec_search_observations(query_embedding, bank_id, top_k=5)
    obs_sem = [
        o for o in obs_sem if o.get("similarity", 0) >= OBS_MIN_SIMILARITY
    ]
    obs_kw = _db.fts_search_observations(bank_id, query, limit=5)
    relevant_obs = reciprocal_rank_fusion(obs_sem, obs_kw, k=60)[:5]

    # A memory already folded into a shown observation is redundant as a
    # Supporting Memory — drop it so it isn't re-displayed (and double-counted)
    # under both the observation and the raw list.
    obs_source_ids = set()
    for o in relevant_obs:
        obs_source_ids.update(o.get("source_memory_ids", []))
    if obs_source_ids:
        top_results = [m for m in top_results if m["id"] not in obs_source_ids]

    if not top_results and not relevant_obs:
        return f"No memories found in bank '{bank_id}' matching: {query}"

    total_count = len(top_results) + len(relevant_obs)

    lines = [
        f"Recall Results for bank '{bank_id}'",
        f"Query: {query}",
        f"Found {total_count} relevant items.\n",
    ]

    # ── Observations first (consolidated knowledge) ──
    if relevant_obs:
        lines.append("── Consolidated Knowledge (Observations) ──\n")
        for obs in relevant_obs:
            lines.append(f"★ {obs['content']}")
            if verbose:
                sim = obs.get("similarity", 0)
                lines.append(f"  (Observation ID: {obs['id']} | Sim: {sim:.4f})\n")
            else:
                lines.append(f"  (Observation ID: {obs['id']})\n")

    # ── Individual memories ──
    if top_results:
        if relevant_obs:
            lines.append("── Supporting Memories ──\n")

        for i, mem in enumerate(top_results, 1):
            ts = mem.get("timestamp", "unknown")
            entities = mem.get("entities", [])
            ent_str = f" | Entities: {', '.join(entities)}" if entities else ""

            lines.append(f"{i}. [{ts}]{ent_str}")
            lines.append(f"   {mem['content']}")
            if verbose:
                sim = mem.get("similarity", mem.get("score", 0))
                rrf = mem.get("rrf_score", 0)
                lines.append(f"   (ID: {mem['id']} | RRF: {rrf:.4f} | Sim: {sim:.4f})\n")
            else:
                lines.append(f"   (ID: {mem['id']})\n")

    return "\n".join(lines)


# ── Tool: Reflect ─────────────────────────────────────────────────────

@mcp.tool()
async def KIROK_reflect(
    bank_id: str,
    query: str,
    limit: int = 20,
    auto_refresh: bool = False,
    source_query: str = "",
) -> str:
    """Reflect on accumulated memories to generate new insights.

    Retrieves relevant memories, analyzes them with an LLM, and saves
    the resulting insight as a 'mental model' for future reference.

    Args:
        bank_id: Memory bank to reflect on.
        query: What to reflect on (question, topic, or open-ended prompt).
        limit: Max memories to consider (default 20, max 100).
        auto_refresh: Whether to refresh this model after future consolidations.
        source_query: Optional query to use for future refreshes. Defaults to query.
    """
    limit = min(max(limit, 1), 100)

    query_embedding = await _embedder.embed(query, task_type=TASK_TYPE_QUERY)
    relevant = _db.vec_search(query_embedding, bank_id, top_k=limit)

    if not relevant:
        return f"No memories found in bank '{bank_id}' to reflect on."

    existing_models = _db.get_mental_models(bank_id, limit=5)

    try:
        async def _do_reflect():
            return await _llm.reflect(
                query=query,
                memories=relevant,
                existing_models=existing_models,
            )

        reflection = await asyncio.wait_for(_do_reflect(), timeout=REFLECT_TIMEOUT)
    except asyncio.TimeoutError:
        return (
            f"Reflect operation timed out after {REFLECT_TIMEOUT} seconds.\n"
            f"Consider reducing the number of memories (current limit: {limit}) "
            f"or simplifying the query.\n"
            f"Timeout can be configured via KIROK_REFLECT_TIMEOUT env var."
        )

    memory_ids = [m["id"] for m in relevant]
    model_id = _db.insert_mental_model_with_options(
        bank_id=bank_id,
        topic=reflection["topic"],
        insight=reflection["insight"],
        based_on=memory_ids,
        auto_refresh=auto_refresh,
        source_query=source_query or query,
    )

    auto_refresh_status = "enabled" if auto_refresh else "disabled"

    return (
        f"Reflection: {reflection['topic']}\n\n"
        f"{reflection['insight']}\n\n"
        f"(Based on {len(relevant)} memories | Model ID: {model_id} | "
        f"Auto-refresh: {auto_refresh_status})\n"
    )


# ── Tool: Consolidate ─────────────────────────────────────────────────

@mcp.tool()
async def KIROK_consolidate(bank_id: str) -> str:
    """Manually trigger observation consolidation for a bank.

    Processes unconsolidated memories and synthesizes them into
    observations — patterns, preferences, and durable knowledge.

    Args:
        bank_id: Memory bank to consolidate.
    """
    try:
        result = await asyncio.wait_for(
            _run_consolidation(bank_id),
            timeout=CONSOLIDATION_TIMEOUT,
        )
        return result
    except asyncio.TimeoutError:
        return (
            f"Consolidation timed out after {CONSOLIDATION_TIMEOUT} seconds.\n"
            f"Timeout can be configured via KIROK_CONSOLIDATION_TIMEOUT env var."
        )
    except Exception as e:
        # A failed consolidation leaves every source memory unconsolidated (the
        # LLM layer raises instead of faking an empty result), so the batch is
        # retried by a later run. Record it so KIROK_stats surfaces the failure.
        logger.warning("Manual consolidation failed for bank '%s': %s", bank_id, e)
        _db.record_failure(bank_id, "manual_consolidation", str(e))
        return (
            f"Consolidation FAILED: {e}\n"
            f"No memories were marked consolidated; the batch will be retried "
            f"on the next consolidation run."
        )


# ── Tool: Set Bank Config ────────────────────────────────────────────

@mcp.tool()
async def KIROK_set_bank_config(
    bank_id: str,
    retain_mission: str = "",
    observations_mission: str = "",
) -> str:
    """Configure a memory bank's retain and observations missions.

    The retain_mission guides what entities/keywords to extract (and what to ignore).
    The observations_mission guides what patterns to consolidate into observations.

    Args:
        bank_id: Memory bank to configure.
        retain_mission: Plain-language description of what this bank should focus on.
        observations_mission: Plain-language description of what observations to synthesize.
    """
    config = _db.set_bank_config(
        bank_id=bank_id,
        retain_mission=retain_mission or None,
        observations_mission=observations_mission or None,
    )

    return (
        f"Bank config updated for '{bank_id}'.\n\n"
        f"- Retain Mission: {config['retain_mission'] or '(default)'}\n"
        f"- Observations Mission: {config['observations_mission'] or '(default)'}\n"
    )


# ── Tool: Get Bank Config ────────────────────────────────────────────

@mcp.tool()
async def KIROK_get_bank_config(bank_id: str) -> str:
    """Get the current configuration for a memory bank.

    Args:
        bank_id: Memory bank to query.
    """
    config = _db.get_bank_config(bank_id)

    return (
        f"Bank config for '{config['bank_id']}':\n\n"
        f"- Retain Mission: {config['retain_mission'] or '(not set — default extraction)'}\n"
        f"- Observations Mission: {config['observations_mission'] or '(not set — default consolidation)'}\n"
    )


# ── Tool: List Banks ──────────────────────────────────────────────────

@mcp.tool()
async def KIROK_list_banks() -> str:
    """List all available memory banks with their memory counts."""
    banks = _db.list_banks()

    if not banks:
        return "No memory banks found. Use KIROK_retain to create your first memory."

    lines = ["Memory Banks:\n"]
    for b in banks:
        lines.append(
            f"- {b['bank_id']}: {b['memory_count']} memories "
            f"({b['oldest'][:10]} to {b['newest'][:10]})"
        )

    return "\n".join(lines)


# ── Tool: Stats ───────────────────────────────────────────────────────

@mcp.tool()
async def KIROK_stats(bank_id: str) -> str:
    """Get statistics for a specific memory bank.

    Args:
        bank_id: Memory bank identifier.
    """
    stats = _db.get_stats(bank_id)
    config = _db.get_bank_config(bank_id)

    lines = [
        f"Stats for '{stats['bank_id']}':",
        f"- Memories: {stats['memory_count']}",
        f"- Mental Models: {stats['mental_model_count']}",
        f"- Observations: {stats['observations_count']}",
        f"- Unconsolidated memories: {stats['unconsolidated_count']}",
        f"- Retain Mission: {'set' if config['retain_mission'] else 'not set'}",
        f"- Observations Mission: {'set' if config['observations_mission'] else 'not set'}",
    ]

    # Surface silent background failures (auto-consolidation, auto-refresh) so
    # the user learns about them instead of finding stale observations later.
    failures = _db.get_recent_failures(bank_id, limit=3)
    if failures:
        lines.append(f"- Recent background failures (newest {len(failures)}):")
        for f in failures:
            detail = f" — {f['detail']}" if f["detail"] else ""
            lines.append(f"  - [{f['created_at'][:19]}] {f['event']}{detail}")
    else:
        lines.append("- Background failures: none recorded")

    # Session-lifetime API usage (see the api_calls counters on the clients).
    # getattr keeps this robust when clients are unset (import-time / tests).
    emb_calls = getattr(_embedder, "api_calls", 0)
    llm_calls = getattr(_llm, "api_calls", 0)
    lines.append(f"- API calls this session: embeddings={emb_calls}, llm={llm_calls}")

    return "\n".join(lines) + "\n"


# ── Tool: Forget ──────────────────────────────────────────────────────

@mcp.tool()
async def KIROK_forget(memory_id: str) -> str:
    """Delete a specific memory by its ID. This is destructive and cannot be undone.

    Args:
        memory_id: ID of the memory to delete.
    """
    deleted = _db.delete_memory(memory_id)

    if deleted:
        return f"Memory {memory_id} has been deleted."
    else:
        return f"Memory {memory_id} not found."


# ── Tool: List Memories ───────────────────────────────────────────────

@mcp.tool()
async def KIROK_list_memories(
    bank_id: str,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """List memories in a bank with pagination, ordered by most recent.

    Args:
        bank_id: Memory bank to browse.
        limit: Maximum number of memories to return (default 20, max 100).
        offset: Number of memories to skip for pagination (default 0).
    """
    limit = min(max(limit, 1), 100)
    memories = _db.list_memories(bank_id, limit=limit, offset=offset)

    if not memories:
        return f"No memories found in bank '{bank_id}'" + (
            f" (offset {offset})" if offset else ""
        )

    lines = [
        f"Memories in '{bank_id}' (showing {len(memories)}, offset {offset})\n",
    ]
    for i, mem in enumerate(memories, offset + 1):
        entities = mem.get("entities", [])
        ent_str = f" | Entities: {', '.join(entities)}" if entities else ""
        lines.append(f"{i}. [{mem['timestamp'][:19]}]{ent_str}")
        lines.append(f"   {mem['content'][:200]}")
        lines.append(f"   (ID: {mem['id']})\n")

    return "\n".join(lines)


# ── Tool: Get Memory ──────────────────────────────────────────────────

@mcp.tool()
async def KIROK_get_memory(memory_id: str) -> str:
    """Get full details of a specific memory by its ID.

    Args:
        memory_id: The memory ID to look up.
    """
    mem = _db.get_memory(memory_id)
    if not mem:
        return f"Memory {memory_id} not found."

    entities_str = ", ".join(mem["entities"]) if mem["entities"] else "(none)"
    keywords_str = ", ".join(mem["keywords"]) if mem["keywords"] else "(none)"

    return (
        f"Memory Details\n\n"
        f"- ID: {mem['id']}\n"
        f"- Bank: {mem['bank_id']}\n"
        f"- Content: {mem['content']}\n"
        f"- Entities: {entities_str}\n"
        f"- Keywords: {keywords_str}\n"
        f"- Context: {mem['context'] or '(none)'}\n"
        f"- Timestamp: {mem['timestamp']}\n"
        f"- Created: {mem['created_at']}\n"
        f"- Metadata: {mem['metadata']}\n"
    )


# ── Tool: Update Memory ───────────────────────────────────────────────

@mcp.tool()
async def KIROK_update_memory(
    memory_id: str,
    content: str = "",
    context: str = "",
) -> str:
    """Update an existing memory's content. Re-extracts entities/keywords
    and regenerates the embedding if content changes.

    Args:
        memory_id: ID of the memory to update.
        content: New content text (leave empty to keep current).
        context: New context string (leave empty to keep current).
    """
    if not content and not context:
        return "No changes specified. Provide content and/or context to update."

    new_content = content or None
    new_context = context or None
    new_entities = None
    new_keywords = None
    new_embedding = None

    if new_content:
        mem = _db.get_memory(memory_id)
        if not mem:
            return f"Memory {memory_id} not found."
        # Mirror _retain_memory: extract under the bank's retain mission so
        # entities/keywords stay consistent with how the memory was first stored.
        mission = _db.get_bank_config(mem["bank_id"]).get("retain_mission", "")
        extraction = await _llm.extract_entities(new_content, mission=mission)
        new_entities = extraction["entities"]
        new_keywords = extraction["keywords"]
        new_embedding = await _embedder.embed(new_content, task_type=TASK_TYPE_DOCUMENT)

    updated = _db.update_memory(
        memory_id=memory_id,
        content=new_content,
        entities=new_entities,
        keywords=new_keywords,
        context=new_context,
        embedding=new_embedding,
    )

    if updated:
        return f"Memory {memory_id} updated successfully."
    else:
        return f"Memory {memory_id} not found."


# ── Tool: Clear Bank ──────────────────────────────────────────────────

@mcp.tool()
async def KIROK_clear_bank(bank_id: str, confirm: bool = False) -> str:
    """Delete ALL memories and observations in a bank, keeping the bank itself.
    Mental models are preserved. This is destructive and cannot be undone.

    Without confirm=true this makes NO changes and returns a preview of what
    would be deleted. Only pass confirm=true after the user has explicitly
    approved deleting this specific bank's contents.

    Args:
        bank_id: Bank to clear.
        confirm: Must be true to actually delete. Defaults to false (preview).
    """
    if not confirm:
        stats = _db.get_stats(bank_id)
        return (
            f"NOT cleared — confirmation required.\n"
            f"This would permanently delete from bank '{bank_id}':\n"
            f"- Memories: {stats['memory_count']}\n"
            f"- Observations: {stats['observations_count']}\n"
            f"Nothing was changed. If the user explicitly approved this, "
            f"call again with confirm=true.\n"
        )

    result = _db.clear_bank(bank_id)
    return (
        f"Cleared bank '{bank_id}'.\n"
        f"- Memories removed: {result['memories_deleted']}\n"
        f"- Observations removed: {result['observations_deleted']}\n"
        f"- Mental models preserved\n"
    )


# ── Tool: Delete Bank ─────────────────────────────────────────────────

@mcp.tool()
async def KIROK_delete_bank(bank_id: str, confirm: bool = False) -> str:
    """Permanently delete a bank and ALL its memories, observations, models, and config.
    This is destructive and cannot be undone.

    Without confirm=true this makes NO changes and returns a preview of what
    would be deleted. Only pass confirm=true after the user has explicitly
    approved deleting this specific bank.

    Args:
        bank_id: Bank to delete entirely.
        confirm: Must be true to actually delete. Defaults to false (preview).
    """
    if not confirm:
        stats = _db.get_stats(bank_id)
        return (
            f"NOT deleted — confirmation required.\n"
            f"This would permanently delete bank '{bank_id}' and all its data:\n"
            f"- Memories: {stats['memory_count']}\n"
            f"- Observations: {stats['observations_count']}\n"
            f"- Mental models: {stats['mental_model_count']}\n"
            f"Nothing was changed. If the user explicitly approved this, "
            f"call again with confirm=true.\n"
        )

    result = _db.delete_bank(bank_id)
    return (
        f"Bank '{bank_id}' deleted.\n"
        f"- Memories removed: {result['memories_deleted']}\n"
        f"- Observations removed: {result['observations_deleted']}\n"
        f"- Mental models removed: {result['models_deleted']}\n"
        f"- Bank config removed: {result['config_deleted']}\n"
    )


# ── Tool: List Mental Models ──────────────────────────────────────────

@mcp.tool()
async def KIROK_list_mental_models(
    bank_id: str,
    limit: int = 10,
) -> str:
    """List mental models (insights generated by Reflect) for a bank.

    Args:
        bank_id: Memory bank to list mental models from.
        limit: Maximum number of models to return (default 10).
    """
    models = _db.get_mental_models(bank_id, limit=limit)

    if not models:
        return f"No mental models found in bank '{bank_id}'. Use KIROK_reflect to generate insights."

    lines = [f"Mental Models in '{bank_id}' ({len(models)} found)\n"]
    for i, m in enumerate(models, 1):
        lines.append(f"{i}. [{m['topic']}]")
        lines.append(f"   {m['insight'][:200]}")
        lines.append(f"   (ID: {m['id']} | Based on {len(m['based_on'])} memories | Updated: {m['updated_at'][:10]})\n")

    return "\n".join(lines)


# ── Tool: Get Mental Model ────────────────────────────────────────────

@mcp.tool()
async def KIROK_get_mental_model(model_id: str) -> str:
    """Get full details of a specific mental model.

    Args:
        model_id: The mental model ID to look up.
    """
    model = _db.get_mental_model(model_id)
    if not model:
        return f"Mental model {model_id} not found."

    based_on_str = ", ".join(model["based_on"]) if model["based_on"] else "(none)"

    return (
        f"Mental Model Details\n\n"
        f"- ID: {model['id']}\n"
        f"- Bank: {model['bank_id']}\n"
        f"- Topic: {model['topic']}\n"
        f"- Insight: {model['insight']}\n"
        f"- Based on memories: {based_on_str}\n"
        f"- Auto-refresh: {'enabled' if model.get('auto_refresh') else 'disabled'}\n"
        f"- Source query: {model.get('source_query') or '(none)'}\n"
        f"- Created: {model['created_at']}\n"
        f"- Updated: {model['updated_at']}\n"
    )


# ── Tool: Delete Mental Model ─────────────────────────────────────────

@mcp.tool()
async def KIROK_delete_mental_model(model_id: str) -> str:
    """Delete a specific mental model. This is destructive and cannot be undone.

    Args:
        model_id: ID of the mental model to delete.
    """
    deleted = _db.delete_mental_model(model_id)
    if deleted:
        return f"Mental model {model_id} has been deleted."
    else:
        return f"Mental model {model_id} not found."


# ── Tool: Refresh Mental Model ────────────────────────────────────────

@mcp.tool()
async def KIROK_refresh_mental_model(
    model_id: str,
    limit: int = 20,
) -> str:
    """Refresh an existing mental model by re-analyzing current memories.
    Updates the insight based on the latest data in the bank.

    Args:
        model_id: ID of the mental model to refresh.
        limit: Max memories to consider (default 20, max 100).
    """
    model = _db.get_mental_model(model_id)
    if not model:
        return f"Mental model {model_id} not found."

    limit = min(max(limit, 1), 100)
    query = model.get("source_query") or model["topic"]
    bank_id = model["bank_id"]

    query_embedding = await _embedder.embed(query, task_type=TASK_TYPE_QUERY)
    relevant = _db.vec_search(query_embedding, bank_id, top_k=limit)

    if not relevant:
        return f"No memories found in bank '{bank_id}' to refresh model with."

    existing_models = _db.get_mental_models(bank_id, limit=5)

    try:
        async def _do_reflect():
            return await _llm.reflect(
                query=query,
                memories=relevant,
                existing_models=existing_models,
            )

        reflection = await asyncio.wait_for(_do_reflect(), timeout=REFLECT_TIMEOUT)
    except asyncio.TimeoutError:
        return (
            f"Refresh operation timed out after {REFLECT_TIMEOUT} seconds.\n"
            f"Consider reducing the number of memories (current limit: {limit}) "
            f"or simplifying the query.\n"
            f"Timeout can be configured via KIROK_REFLECT_TIMEOUT env var."
        )

    memory_ids = [m["id"] for m in relevant]
    _db.update_mental_model(
        model_id=model_id,
        topic=reflection["topic"],
        insight=reflection["insight"],
        based_on=memory_ids,
    )

    return (
        f"Mental model refreshed: {reflection['topic']}\n\n"
        f"{reflection['insight']}\n\n"
        f"(Based on {len(relevant)} memories | Model ID: {model_id})\n"
    )


# ── Entry Point ───────────────────────────────────────────────────────

def main():
    """Run the Kirok MCP server."""
    logging.basicConfig(
        level=logging.INFO, format="%(name)s %(levelname)s: %(message)s"
    )
    _init_runtime()
    mcp.run()


if __name__ == "__main__":
    main()
