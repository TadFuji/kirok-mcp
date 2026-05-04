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

from kirok_mcp.db import MemoryDB
from kirok_mcp.embeddings import (
    EmbeddingClient,
    reciprocal_rank_fusion,
    semantic_search,
)
from kirok_mcp.llm import LLMClient

# ── Load environment ──────────────────────────────────────────────────

_pkg_dir = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.dirname(os.path.dirname(_pkg_dir))
load_dotenv(os.path.join(_project_dir, ".env"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DB_PATH = os.environ.get("KIROK_DB_PATH", None)
REFLECT_TIMEOUT = int(os.environ.get("KIROK_REFLECT_TIMEOUT", "300"))
CONSOLIDATION_TIMEOUT = int(os.environ.get("KIROK_CONSOLIDATION_TIMEOUT", "120"))

if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY environment variable is required.", file=sys.stderr)
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("kirok.server")

# ── Module-level singletons ──────────────────────────────────────────

_db = MemoryDB(db_path=DB_PATH)
_db.connect()
atexit.register(_db.close)

_embedder = EmbeddingClient(api_key=GEMINI_API_KEY)
_llm = LLMClient(api_key=GEMINI_API_KEY)

mcp = FastMCP("kirok_mcp")


# ── Internal: Deduplication Threshold ─────────────────────────────────

DEDUP_SIMILARITY_THRESHOLD = float(
    os.environ.get("KIROK_DEDUP_THRESHOLD", "0.85")
)


# ── Internal: Consolidation Engine ────────────────────────────────────

async def _run_consolidation(bank_id: str) -> str:
    """Run observation consolidation for a bank.

    Finds unconsolidated memories, compares against existing observations,
    and creates/updates/deletes observations as needed. Also auto-refreshes
    mental models with auto_refresh=True.

    Returns a summary string of what happened.
    """
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

    for action in actions:
        if action["action"] == "delete" and action.get("observation_id"):
            # DELETE: Remove contradicted/obsolete observation
            if _db.delete_observation(action["observation_id"]):
                deleted_count += 1
                logger.info(
                    "Observation deleted: %s (reason: %s)",
                    action["observation_id"],
                    action.get("content", "no reason"),
                )
            continue

        # Generate embedding for create/update
        obs_embedding = await _embedder.embed(action["content"])

        if action["action"] == "create":
            _db.insert_observation(
                bank_id=bank_id,
                content=action["content"],
                source_memory_ids=action["source_memory_ids"],
                embedding=obs_embedding,
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
                        embedding=obs_embedding,
                    )
                    updated_count += 1
                    break

    # Mark memories as consolidated
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
                query_emb = await _embedder.embed(query)
                all_mems = _db.get_all_embeddings(bank_id)
                relevant = semantic_search(query_emb, all_mems, top_k=20)
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

    embedding = await _embedder.embed(content)

    # ── Smart Deduplication: check for similar existing memories ──
    all_memories = _db.get_all_embeddings(bank_id)
    if all_memories:
        similar = semantic_search(embedding, all_memories, top_k=5)
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

                # Re-extract entities for merged content
                extraction = await _llm.extract_entities(merged, mission=mission)
                merged_emb = await _embedder.embed(merged)

                updated = _db.update_memory(
                    memory_id=target_id,
                    content=merged,
                    entities=extraction["entities"],
                    keywords=extraction["keywords"],
                    context=context or None,
                    embedding=merged_emb,
                )

                if updated:
                    result = (
                        f"Memory UPDATED (enriched existing).\n\n"
                        f"- Action: UPDATE\n"
                        f"- Reason: {dedup_result['reason']}\n"
                        f"- Updated ID: {target_id}\n"
                        f"- Entities: {', '.join(extraction['entities']) or '(none)'}\n"
                        f"- Keywords: {', '.join(extraction['keywords']) or '(none)'}\n"
                    )

                    # Trigger consolidation after update
                    try:
                        consolidation_result = await asyncio.wait_for(
                            _run_consolidation(bank_id),
                            timeout=CONSOLIDATION_TIMEOUT,
                        )
                        result += f"\n📊 Auto-Consolidation:\n{consolidation_result}\n"
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning("Auto-consolidation after update: %s", e)

                    return result
                # If update failed (ID not found), fall through to ADD

    # ── Normal ADD flow ──
    extraction = await _llm.extract_entities(content, mission=mission)

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

    # Trigger background consolidation (fire-and-forget, non-blocking)
    try:
        consolidation_result = await asyncio.wait_for(
            _run_consolidation(bank_id),
            timeout=CONSOLIDATION_TIMEOUT,
        )
        result += f"\n📊 Auto-Consolidation:\n{consolidation_result}\n"
    except asyncio.TimeoutError:
        logger.warning("Auto-consolidation timed out for bank '%s'", bank_id)
        result += "\n⚠️ Auto-consolidation timed out (memories will be consolidated on next retain).\n"
    except Exception as e:
        logger.warning("Auto-consolidation failed for bank '%s': %s", bank_id, e)
        result += "\n⚠️ Auto-consolidation encountered an error (memory was still saved).\n"

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

    evaluation = await _llm.evaluate_importance(content, mission=mission)

    if not evaluation["should_retain"] or evaluation["score"] < threshold:
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

@mcp.tool()
async def KIROK_recall(
    bank_id: str,
    query: str,
    limit: int = 10,
    time_min: str = "",
    time_max: str = "",
) -> str:
    """Search and retrieve relevant memories using semantic similarity
    and keyword matching, merged with Reciprocal Rank Fusion.

    Args:
        bank_id: Memory bank to search.
        query: Natural language search query.
        limit: Maximum number of results (default 10, max 50).
        time_min: Optional ISO 8601 lower bound for timestamp filtering.
        time_max: Optional ISO 8601 upper bound for timestamp filtering.
    """
    limit = min(max(limit, 1), 50)

    # Reject empty / whitespace-only queries early
    if not query or not query.strip():
        return "Error: query must not be empty. Please provide a search term."

    query_embedding = await _embedder.embed(query)
    all_memories = _db.get_all_embeddings(bank_id)

    # Apply time filtering before semantic search if specified
    if time_min or time_max:
        filtered = []
        for m in all_memories:
            ts = m.get("timestamp", "")
            if time_min and ts < time_min:
                continue
            if time_max and ts > time_max:
                continue
            filtered.append(m)
        all_memories = filtered

    semantic_results = semantic_search(query_embedding, all_memories, top_k=limit)

    fts_results = _db.fts_search(bank_id, query, limit=limit)
    # Apply time filtering to FTS results as well
    if time_min or time_max:
        time_mems = _db.search_by_timestamp(
            bank_id, time_min=time_min or None, time_max=time_max or None, limit=limit * 2
        )
        time_ids = {m["id"] for m in time_mems}
        fts_results = [r for r in fts_results if r["id"] in time_ids]

    merged = reciprocal_rank_fusion(semantic_results, fts_results, k=60)
    top_results = merged[:limit]

    # ── Observation-first display (inspired by Mem0 knowledge layer) ──
    obs_embeddings = _db.get_observation_embeddings(bank_id)
    relevant_obs = []
    if obs_embeddings:
        obs_results = semantic_search(query_embedding, obs_embeddings, top_k=5)
        relevant_obs = [o for o in obs_results if o.get("similarity", 0) > 0.4]

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
        for i, obs in enumerate(relevant_obs, 1):
            sim = obs.get("similarity", 0)
            lines.append(f"★ {obs['content']}")
            lines.append(f"  (Observation ID: {obs['id']} | Sim: {sim:.4f})\n")

    # ── Individual memories ──
    if top_results:
        if relevant_obs:
            lines.append("── Supporting Memories ──\n")

        for i, mem in enumerate(top_results, 1):
            sim = mem.get("similarity", mem.get("score", 0))
            rrf = mem.get("rrf_score", 0)
            ts = mem.get("timestamp", "unknown")
            entities = mem.get("entities", [])
            ent_str = f" | Entities: {', '.join(entities)}" if entities else ""

            lines.append(f"{i}. [{ts}]{ent_str}")
            lines.append(f"   {mem['content']}")
            lines.append(f"   (ID: {mem['id']} | RRF: {rrf:.4f} | Sim: {sim:.4f})\n")

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

    query_embedding = await _embedder.embed(query)
    all_memories = _db.get_all_embeddings(bank_id)
    relevant = semantic_search(query_embedding, all_memories, top_k=limit)

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
    obs_count = len(_db.get_observations(bank_id, limit=1000))
    uncons = len(_db.get_unconsolidated_memories(bank_id, limit=1000))
    config = _db.get_bank_config(bank_id)

    return (
        f"Stats for '{stats['bank_id']}':\n"
        f"- Memories: {stats['memory_count']}\n"
        f"- Mental Models: {stats['mental_model_count']}\n"
        f"- Observations: {obs_count}\n"
        f"- Unconsolidated memories: {uncons}\n"
        f"- Retain Mission: {'set' if config['retain_mission'] else 'not set'}\n"
        f"- Observations Mission: {'set' if config['observations_mission'] else 'not set'}\n"
    )


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
        extraction = await _llm.extract_entities(new_content)
        new_entities = extraction["entities"]
        new_keywords = extraction["keywords"]
        new_embedding = await _embedder.embed(new_content)

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
async def KIROK_clear_bank(bank_id: str) -> str:
    """Delete ALL memories and observations in a bank, keeping the bank itself.
    Mental models are preserved. This is destructive and cannot be undone.

    Args:
        bank_id: Bank to clear.
    """
    result = _db.clear_bank(bank_id)
    return (
        f"Cleared bank '{bank_id}'.\n"
        f"- Memories removed: {result['memories_deleted']}\n"
        f"- Observations removed: {result['observations_deleted']}\n"
        f"- Mental models preserved\n"
    )


# ── Tool: Delete Bank ─────────────────────────────────────────────────

@mcp.tool()
async def KIROK_delete_bank(bank_id: str) -> str:
    """Permanently delete a bank and ALL its memories, observations, models, and config.
    This is destructive and cannot be undone.

    Args:
        bank_id: Bank to delete entirely.
    """
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

    query_embedding = await _embedder.embed(query)
    all_memories = _db.get_all_embeddings(bank_id)
    relevant = semantic_search(query_embedding, all_memories, top_k=limit)

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
    mcp.run()


if __name__ == "__main__":
    main()
