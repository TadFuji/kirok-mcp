"""Gemini LLM wrapper for entity extraction, reflection, and consolidation.

Uses gemini-2.5-flash-lite for lightweight LLM tasks:
- Extracting entities and keywords from text (Retain)
- Generating insights from accumulated memories (Reflect)
- Consolidating memories into observations (Consolidate)
- Evaluating content importance for smart retain
"""

import json
import logging

from google import genai


LLM_MODEL = "gemini-2.5-flash-lite"

logger = logging.getLogger("kirok.llm")


def _to_str_list(items: list) -> list[str]:
    """Normalize a list that may contain dicts or nested structures to flat strings."""
    result = []
    for item in items:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            val = item.get("name") or item.get("entity") or item.get("keyword")
            if val:
                result.append(str(val))
            else:
                for v in item.values():
                    if isinstance(v, str):
                        result.append(v)
                        break
        else:
            result.append(str(item))
    return result


def _parse_json_response(raw: str) -> dict | None:
    """Parse LLM response, stripping markdown fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class LLMClient:
    """Wrapper for Gemini chat API for memory processing tasks."""

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)

    async def extract_entities(
        self, text: str, mission: str = ""
    ) -> dict:
        """Extract entities and keywords from text for memory indexing.

        Args:
            text: The text to analyze.
            mission: Optional retain mission to guide extraction focus.

        Returns:
            dict with keys: entities (list[str]), keywords (list[str])
        """
        mission_block = ""
        if mission:
            mission_block = f"""
IMPORTANT — Extraction Mission:
{mission}

Apply this mission when deciding which entities and keywords to extract.
Focus on items relevant to the mission. Skip items the mission says to ignore.
"""

        prompt = f"""Analyze the following text and extract structured information.
Return a JSON object with exactly these keys:
- "entities": list of named entities (people, organizations, places, products)
- "keywords": list of important keywords and concepts (not entities)
{mission_block}
Text:
{text}

Respond with ONLY valid JSON, no markdown formatting, no explanation."""

        response = self.client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
        )

        result = _parse_json_response(response.text)
        if result:
            return {
                "entities": _to_str_list(result.get("entities", [])),
                "keywords": _to_str_list(result.get("keywords", [])),
            }
        return {"entities": [], "keywords": []}

    async def reflect(
        self,
        query: str,
        memories: list[dict],
        existing_models: list[dict] | None = None,
    ) -> dict:
        """Generate insights by reflecting on accumulated memories.

        Args:
            query: The reflection query or topic.
            memories: Retrieved relevant memories.
            existing_models: Existing mental models for context.

        Returns:
            dict with keys: topic (str), insight (str)
        """
        memory_text = "\n".join(
            f"- [{m.get('timestamp', 'unknown')}] {m['content']}"
            for m in memories
        )

        model_text = ""
        if existing_models:
            model_text = "\n\nExisting Mental Models:\n" + "\n".join(
                f"- [{m['topic']}] {m['insight']}" for m in existing_models
            )

        prompt = f"""You are analyzing a set of memories to generate insights.

Query: {query}

Memories:
{memory_text}
{model_text}

Based on these memories, provide a thoughtful analysis. Consider:
1. Patterns and connections between memories
2. What can be inferred beyond what is explicitly stated
3. Practical implications or recommendations

Return a JSON object with exactly these keys:
- "topic": a concise topic label for this insight (max 10 words)
- "insight": your detailed analysis and insights (comprehensive but focused)

Respond with ONLY valid JSON, no markdown formatting, no explanation."""

        response = self.client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
        )

        result = _parse_json_response(response.text)
        if result:
            return {
                "topic": result.get("topic", query[:50]),
                "insight": result.get("insight", "Unable to generate insight."),
            }
        return {
            "topic": query[:50],
            "insight": f"Reflection on: {query}\n\nBased on {len(memories)} memories. (LLM output parsing failed)",
        }

    # ── Observation Consolidation ─────────────────────────────────────

    async def consolidate(
        self,
        new_memories: list[dict],
        existing_observations: list[dict],
        observations_mission: str = "",
    ) -> list[dict]:
        """Consolidate new memories into observations.

        Compares new memories against existing observations and determines:
        - CREATE: New observation from patterns in new memories
        - UPDATE: Existing observation needs refinement with new evidence
        - SKIP: No new pattern worth capturing

        Args:
            new_memories: Memories not yet consolidated.
            existing_observations: Current observations in the bank.
            observations_mission: Optional mission guiding what to observe.

        Returns:
            List of dicts with keys:
              - action: "create" | "update" | "delete"
              - content: The observation text (or deletion reason for "delete")
              - observation_id: (for "update"/"delete") ID of observation
              - source_memory_ids: List of memory IDs that support this
        """
        if not new_memories:
            return []

        memories_text = "\n".join(
            f"- [ID:{m['id']}] [{m.get('timestamp', '?')}] {m['content']}"
            for m in new_memories
        )

        obs_text = ""
        if existing_observations:
            obs_text = "\n\nExisting Observations:\n" + "\n".join(
                f"- [ID:{o['id']}] {o['content']}"
                for o in existing_observations
            )

        mission_block = ""
        if observations_mission:
            mission_block = f"""
Observations Mission:
{observations_mission}
"""

        prompt = f"""You are a knowledge consolidation engine. Your job is to
synthesize patterns and durable knowledge from individual memories.

New Memories to Consolidate:
{memories_text}
{obs_text}
{mission_block}
Instructions:
1. Look for patterns, preferences, decisions, or durable facts in the new memories.
2. If a new memory reinforces or extends an existing observation, UPDATE it.
3. If a new memory reveals a new pattern not covered by existing observations, CREATE a new one.
4. If a new memory CONTRADICTS an existing observation and makes it clearly obsolete or wrong, DELETE it.
5. If a new memory partially contradicts an observation, UPDATE it with nuanced understanding.
6. Skip trivial, one-off, or ephemeral information (greetings, acknowledgments, temporary states).
7. Each observation should be a self-contained, meaningful statement of knowledge.

Return a JSON array. Each element must have:
- "action": "create", "update", or "delete"
- "content": the observation text (for create/update) or deletion reason (for delete)
- "observation_id": (required for "update" and "delete") the ID of the existing observation
- "source_memory_ids": array of memory IDs from the new memories that support this

If no observations should be created, updated, or deleted, return an empty array: []

Respond with ONLY valid JSON, no markdown formatting, no explanation."""

        try:
            response = self.client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
            )

            result = _parse_json_response(response.text)
            if result is None:
                logger.warning("Consolidation LLM returned unparseable response")
                return []

            if not isinstance(result, list):
                logger.warning("Consolidation LLM returned non-array: %s", type(result))
                return []

            # Validate each item
            validated = []
            for item in result:
                if not isinstance(item, dict):
                    continue
                action = item.get("action")
                content = item.get("content")
                if action not in ("create", "update", "delete") or not content:
                    continue
                if action == "update" and not item.get("observation_id"):
                    # Missing observation_id for update — treat as create
                    item["action"] = "create"
                if action == "delete" and not item.get("observation_id"):
                    # Cannot delete without target — skip
                    continue
                validated.append({
                    "action": item["action"],
                    "content": item["content"],
                    "observation_id": item.get("observation_id", ""),
                    "source_memory_ids": item.get("source_memory_ids", []),
                })

            return validated

        except Exception as e:
            logger.error("Consolidation failed: %s", e)
            return []

    # ── Importance Evaluation ─────────────────────────────────────────

    async def evaluate_importance(
        self, content: str, mission: str = ""
    ) -> dict:
        """Evaluate whether content is worth retaining.

        Returns:
            dict with keys:
              - score (int 1-10): importance score
              - reason (str): brief explanation
              - should_retain (bool): True if score >= 5
        """
        mission_block = ""
        if mission:
            mission_block = f"""
Retention Mission:
{mission}

Evaluate importance against this mission.
"""

        prompt = f"""You are a memory importance evaluator. Score the following
content from 1 to 10 based on its long-term value.

Scoring Guide:
- 1-3: Ephemeral, trivial, or routine (greetings, acknowledgments, temporary states)
- 4-5: Mildly useful but likely outdated within weeks
- 6-7: Meaningful decision, preference, or useful fact
- 8-10: Critical learning, major decision, or durable knowledge
{mission_block}
Content to evaluate:
{content}

Return a JSON object with:
- "score": integer 1-10
- "reason": brief explanation (1 sentence)

Respond with ONLY valid JSON, no markdown formatting, no explanation."""

        try:
            response = self.client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
            )

            result = _parse_json_response(response.text)
            if result and isinstance(result.get("score"), (int, float)):
                score = int(result["score"])
                return {
                    "score": score,
                    "reason": result.get("reason", ""),
                    "should_retain": score >= 5,
                }
        except Exception as e:
            logger.error("Importance evaluation failed: %s", e)

        # Default: retain (fail-open to avoid losing information)
        return {"score": 5, "reason": "Evaluation failed, defaulting to retain", "should_retain": True}

    # ── Smart Deduplication (inspired by Mem0) ────────────────────────

    async def deduplicate(
        self,
        new_content: str,
        similar_memories: list[dict],
        mission: str = "",
    ) -> dict:
        """Decide how to handle new content vs existing similar memories.

        Inspired by Mem0's ADD/UPDATE/DELETE/NOOP pipeline, this method
        compares new content against semantically similar existing memories
        and decides the best action to maintain a clean knowledge base.

        Args:
            new_content: The new content to potentially retain.
            similar_memories: Existing memories with high cosine similarity.
            mission: Optional retain mission for context.

        Returns:
            dict with keys:
              - action: "add" | "update" | "noop"
              - reason: Brief explanation of the decision
              - target_memory_id: (for "update") ID of memory to update
              - merged_content: (for "update") The merged content text
        """
        if not similar_memories:
            return {"action": "add", "reason": "No similar memories found"}

        memories_text = "\n".join(
            f"- [ID:{m['id']}] [Sim:{m.get('similarity', 0):.3f}] {m['content']}"
            for m in similar_memories
        )

        mission_block = ""
        if mission:
            mission_block = f"\nRetention Mission: {mission}\n"

        prompt = f"""You are a memory deduplication engine. Compare the new content
against existing similar memories and decide the best action.
{mission_block}
New Content:
{new_content}

Similar Existing Memories:
{memories_text}

Choose ONE action:
- "add": The new content contains genuinely NEW information not covered by existing memories. Add it as a new memory.
- "update": The new content EXTENDS, ENRICHES, or CORRECTS an existing memory. Merge them into one improved memory.
- "noop": The new content is REDUNDANT — it is already fully covered by an existing memory. Skip it.

Rules:
- Prefer "noop" if the new content is essentially a restatement of existing knowledge.
- Prefer "update" if the new content adds complementary details to an existing memory.
- Prefer "add" only if the new content is truly novel.
- For "update", provide the merged content that combines the best of both.

Return a JSON object with:
- "action": "add" | "update" | "noop"
- "reason": brief explanation (1 sentence)
- "target_memory_id": (required for "update") the ID of the memory to update
- "merged_content": (required for "update") the combined content text

Respond with ONLY valid JSON, no markdown formatting, no explanation."""

        try:
            response = self.client.models.generate_content(
                model=LLM_MODEL,
                contents=prompt,
            )

            result = _parse_json_response(response.text)
            if result and result.get("action") in ("add", "update", "noop"):
                action = result["action"]
                resp = {
                    "action": action,
                    "reason": result.get("reason", ""),
                }
                if action == "update":
                    if not result.get("target_memory_id"):
                        # Fallback: no target ID → treat as add
                        resp["action"] = "add"
                        resp["reason"] += " (no target ID provided, treating as add)"
                    else:
                        resp["target_memory_id"] = result["target_memory_id"]
                        resp["merged_content"] = result.get("merged_content", new_content)
                return resp

        except Exception as e:
            logger.error("Deduplication failed: %s", e)

        # Default: add (fail-open to avoid losing information)
        return {"action": "add", "reason": "Deduplication failed, defaulting to add"}
