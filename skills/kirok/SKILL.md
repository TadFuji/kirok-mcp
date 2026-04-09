---
name: kirok
description: Persistent agent memory via the Kirok MCP server (Retain/Recall/Reflect). Use this skill whenever storing learnings, recalling context before tasks, reflecting on accumulated knowledge, managing memory banks, or working with mental models. Trigger when memory, remembering, learning, preferences, or past experiences are relevant.
---

# Kirok Memory System

Persistent memory via the Kirok MCP server (19 tools). **Proactively store learnings and recall context** to provide better assistance across sessions.

> **Note**: Kirok (記録) is the rebranded successor to Hindsight. All tool names now use the `KIROK_` prefix. The underlying database and architecture remain the same.

## How It Works

When `KIROK_retain` is called, the server runs an internal pipeline:

1. **Loads bank config** — applies the Retain Mission (if set) to guide extraction
2. **Extracts entities** (people, tools, concepts) and keywords using Gemini LLM
3. **Generates embeddings** via `gemini-embedding-001` for semantic search
4. **Indexes for FTS5** full-text search (BM25)
5. **Auto-consolidates** — compares new memories against existing Observations, creating/updating patterns

Pass **rich, full-context content** — the server extracts what matters better than a pre-summarized string. Decide **when** to store, not **what** to extract.

## Autonomous Learning (v1.0.0)

Kirok has **three autonomous features** inspired by the upstream vectorize-io/hindsight:

### 1. Retain Mission (記憶フィルタリング)
Set a plain-language mission per bank to guide what to extract and what to ignore:
```
KIROK_set_bank_config(
    bank_id="antigravity",
    retain_mission="Focus on technical decisions, architecture choices, and error solutions. Ignore greetings, acknowledgments, and temporary states.",
    observations_mission="Synthesize durable patterns about system architecture, user preferences, and recurring technical issues."
)
```

### 2. Observation Consolidation (自動知識統合)
After each `retain()`, the server automatically:
- Compares new memories against existing Observations
- Detects patterns and creates new Observations
- Updates existing Observations when new evidence reinforces or contradicts them
- Preserves history when contradictions arise (nuanced understanding)
- Recalls include relevant Observations alongside raw memories

### 3. Smart Retain (重要度フィルタリング)
Use `KIROK_smart_retain` for bulk/automatic ingestion:
```
KIROK_smart_retain(
    bank_id="work-history",
    content="Long conversation content...",
    threshold=6  # Only retain if importance score >= 6/10
)
```

## Bank Design

| Bank | Purpose | Examples |
|------|---------|---------|
| `antigravity` | System knowledge, configuration, architecture | Tool updates, skill changes, constitution changes |
| `user-prefs` | 藤川さんの preferences and conventions | Coding style, communication style, workflow preferences |
| `openclaw` | OpenClaw-specific knowledge | Config changes, troubleshooting history, version notes |
| `work-history` | Cross-project work log | Milestones, deliverables, significant decisions |
| `docs-cache` | docs-intelligence skill managed cache | API docs cache (auto-managed, do not retain manually) |

### Bank Creation Gate
**Do NOT create new banks** without meeting ALL of these criteria:
1. **5+ memories** would immediately go into the new bank
2. **Explicit user approval** obtained before creation
3. **No existing bank** covers the domain (check the table above first)

Fragmentation is the #1 enemy of recall quality. When in doubt, use an existing bank with a descriptive `context` parameter.

## When to Retain

**Always store after learning something valuable:**

- **User Preferences**: Coding style, tool preferences, communication preferences, project conventions
- **Procedure Outcomes**: Steps that worked (or failed) and why, workarounds, configurations that resolved issues
- **Learnings**: Bugs and solutions, performance optimizations, architecture decisions and rationale, dependency/version requirements
- **System Changes**: Constitution updates, skill additions/modifications, tool configuration changes

### Retain Examples

```
# Rich context — let the server extract the facts
KIROK_retain(bank_id="user-prefs",
  content="藤川さんはコードコメントとcommit messageは英語、ユーザーへの説明は日本語を好む。技術引用（ログ、スタックトレース等）は原文のまま保持する。Desu/Masu形式のpolite Japaneseが標準。",
  context="language policy")

# Include outcomes, not just actions
KIROK_retain(bank_id="openclaw",
  content="OpenClaw watchdog のWindows Scheduled Task登録で、VBSラッパー経由のサイレント実行パターンを採用。直接PowerShellだとコンソールウィンドウが表示されてしまう問題を回避。register-tasks.ps1 → check-openclaw-updates.vbs → check-openclaw-updates.ps1 の3段構成。",
  context="architecture decision")
```

### Structured Retain Guidelines (inspired by DeerFlow Memory System)

**Context parameter categorization** — Use the `context` parameter consistently to classify memories:

| Context Category | When to Use | Examples |
|-----------------|-------------|----------|
| `preference` | User likes/dislikes, style choices | "Japanese output, English code comments" |
| `knowledge` | Expertise, mastered techniques | "WSL PATH fix via BASH_ENV" |
| `architecture decision` | Design choices and rationale | "VBS wrapper for silent PowerShell execution" |
| `behavior` | Working patterns, habits | "Prefers small focused PRs over large ones" |
| `goal` | Stated objectives, targets | "Migrate chigasaki.tv to Cloudflare Pages" |
| `system update` | Tool/config/version changes | "Kirok v1.0.0 migration from Hindsight" |
| `troubleshooting` | Problem → root cause → fix | "OpenClaw JSON parse error from malformed CLI output" |

**Deduplication awareness** — Before retaining, briefly recall the same bank+topic to avoid storing duplicate facts. One updated memory is better than three overlapping ones.

**What NOT to retain** (session-scoped information that pollutes long-term memory):
- Temporary file paths or URLs that will expire
- Intermediate debugging outputs after the root cause is found (retain only the root cause + fix)
- Speculative plans that were not approved or executed
- Raw tool outputs — retain the insight, not the data

### Kirok と KI の役割分担

→ **`post-task-recording` スキル参照**。詳細な判定フロー・品質チェックリストはそちらに集約。
基本原則: Kirok =「なぜ？」（教訓）、KI =「どうやる？」（手順）。重複しない。

## When to Recall

**Always recall before:**

- Starting any non-trivial task
- Making implementation decisions
- Suggesting tools, libraries, or approaches
- Working in a new area of the project
- Answering questions about past work or preferences

### Recall Examples

```
# Before starting work
KIROK_recall(bank_id="user-prefs", query="coding preferences and conventions")

# Time-filtered search
KIROK_recall(bank_id="antigravity", query="system changes", time_min="2026-03-15")

# Before debugging
KIROK_recall(bank_id="openclaw", query="similar errors or troubleshooting")
```

## When to Reflect

Use `KIROK_reflect` for **synthesis and pattern recognition**, not simple retrieval:

- Consolidating learnings after a series of related tasks
- Answering "what have we learned about X?" questions
- Building strategic understanding from accumulated experiences
- Periodic review (e.g., "What patterns have emerged in our workflow?")

Reflect creates a **Mental Model** that persists for future reference. Use `KIROK_refresh_mental_model` to update existing models with new evidence.

## Tool Reference

### Core Memory
| Tool | Use |
|------|-----|
| `KIROK_retain` | Store information (auto-extracts entities/keywords/embeddings, auto-consolidates) |
| `KIROK_smart_retain` | Evaluate importance → only retain if score ≥ threshold (for bulk ingestion) |
| `KIROK_recall` | Search memories + observations (semantic + BM25 + RRF fusion, time filters) |
| `KIROK_reflect` | Analyze memories → generate Mental Model insight |
| `KIROK_consolidate` | Manually trigger observation consolidation for a bank |

### Bank Configuration
| Tool | Use |
|------|-----|
| `KIROK_set_bank_config` | Set retain_mission and observations_mission for a bank |
| `KIROK_get_bank_config` | View current bank configuration |

### Memory Browsing
| Tool | Use |
|------|-----|
| `KIROK_list_memories` | Browse bank contents (paginated, newest first) |
| `KIROK_get_memory` | Full details of a specific memory |
| `KIROK_update_memory` | Edit content (re-extracts entities, regenerates embedding) |
| `KIROK_forget` | Delete a single memory |

### Bank Management
| Tool | Use |
|------|-----|
| `KIROK_list_banks` | List all banks with counts |
| `KIROK_stats` | Memory/model/observation counts and config status |
| `KIROK_clear_bank` | Delete all memories in a bank (keeps mental models) |
| `KIROK_delete_bank` | Permanently delete a bank and all its data |

### Mental Models
| Tool | Use |
|------|-----|
| `KIROK_list_mental_models` | List insights generated by Reflect |
| `KIROK_get_mental_model` | Full details of a mental model |
| `KIROK_delete_mental_model` | Delete a mental model |
| `KIROK_refresh_mental_model` | Re-analyze with latest memories |

## Memory Hygiene

- **Retain immediately** — don't defer, context is richest right after learning
- **Recall before acting** — check for existing knowledge before starting work
- **Reflect periodically** — consolidate learnings into mental models
- **Set bank missions** — configure retain and observations missions for your most-used banks
- **Record every error resolution** — when a problem is solved, retain: (1) what happened, (2) root cause, (3) how it was fixed, (4) prevention measures. Use `context: troubleshooting`. "Fixed" is not done; "Fixed AND recorded" is done
- **Review and prune** — use `KIROK_list_memories` to spot duplicates or outdated entries; `KIROK_update_memory` to correct, `KIROK_forget` to remove
- **Don't over-retain** — store decisions and outcomes, not routine steps. One rich memory beats ten shallow ones

## Gotchas

- ❌ Don't pre-summarize content before retaining. The server's entity extraction pipeline works better on raw, rich text than on condensed bullet points
- ✅ ~~Don't use FTS5 special syntax in recall queries~~ → **Fixed in v0.3.0**: `_sanitize_fts_query()` automatically escapes FTS5 operators (AND, OR, NOT, NEAR) and wraps tokens in double quotes. FTS5 parse errors are caught and fall back to empty (semantic search still works via RRF)
- ✅ ~~Don't include dates with hyphens in recall queries~~ → **Fixed in v0.3.0**: Hyphens are replaced with spaces before FTS5 processing. Date filtering should still use `time_min`/`time_max` parameters for best results
- ⚠️ Recall loads ALL embeddings for brute-force cosine similarity. With thousands of memories, consider using `time_min`/`time_max` to narrow scope
- ⚠️ `KIROK_clear_bank` and `KIROK_delete_bank` are destructive with no undo. Always confirm with the user before executing (SMART AUTONOMY: Destructive Change)
- ⚠️ When updating a memory, if content changes, a Gemini API call is made for entity extraction + embedding. Context-only updates are cheaper (no API call)
- ⚠️ `KIROK_reflect` and `KIROK_refresh_mental_model` have a wall-clock timeout (default 300s, configurable via `KIROK_REFLECT_TIMEOUT` env var). If timeout occurs, consider reducing `limit` or simplifying the query
- ⚠️ Empty/whitespace-only queries to `KIROK_recall` are rejected early with an error message. Always provide meaningful search terms
- ⚠️ Auto-consolidation runs within `KIROK_retain` and has its own timeout (default 120s via `KIROK_CONSOLIDATION_TIMEOUT`). If it times out, memories are still saved — consolidation will run on the next retain
- ⚠️ `KIROK_smart_retain` makes an extra LLM call for importance evaluation. Use regular `KIROK_retain` when you're confident the content is worth keeping
