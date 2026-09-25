---
name: kirok
description: The ultimate, autonomous memory skill for the Kirok MCP server. Teaches the AI to proactively manage long-term memory (Retain, Recall, Reflect) without explicit user prompting. Establishes strict bank taxonomy and operational rules.
---

# Kirok: Fully Autonomous Memory Protocol

Kirok (記録) gives you persistent, cross-session memory through its MCP tools — a "second brain" you manage proactively: record durable facts and preferences without waiting to be asked, and check memory before tasks that may depend on past context.

> **Note**: Tools are prefixed with `KIROK_` (e.g., `KIROK_retain`, `KIROK_recall`).

---

## 🛑 Core Operational Directives

1. **Pre-flight Recall (The "Look Before You Leap" Rule)**
   When a task may touch past decisions, user preferences, or known bugs — debugging, writing a new script, planning an architecture — run `KIROK_recall` first.
2. **Post-Task Retain (The "Record the Lesson" Rule)**
   When a bug is fixed, a preference is stated, or a milestone is reached, record it with `KIROK_retain`. If the fact contains credentials, personal data, or anything the user has not agreed to store, check with them before writing it.
3. **Periodic Reflection (The "Wisdom" Rule)**
   If a series of complex tasks has been completed, consider using `KIROK_reflect` to synthesize underlying patterns into mental models.

---

## 🏦 Bank Taxonomy & Fragmentation Prevention

To prevent memory fragmentation (which destroys recall accuracy), keep banks to a small, stable set with broad purposes — do not create arbitrary one-off banks. **If your host environment already defines a bank scheme** (for example via a project's `CLAUDE.md`, an existing configuration, or a set of banks already in use), follow that scheme. Otherwise, use these general-purpose banks as a sensible default:

| Bank ID | Purpose | Example Triggers |
| :--- | :--- | :--- |
| `user-prefs` | User personal preferences and rules | Coding style, language policy (e.g., Japanese for UI), workflow habits |
| `architecture` | System design and technical specs | Tool versions, specific frameworks in use, core logic choices |
| `troubleshooting` | Error logs, root causes, and fixes | "We found why X fails, we must use workaround Y." |
| `milestones` | Project achievements and work history | "Successfully migrated to Next.js on 2026-04-10" |
| `scratch` | Temporary or volatile memory | Unfinished ideas, pending tasks that don't need permanent record |

*Before creating a new bank, check whether an existing one already covers the purpose. Do not create project-specific banks for tiny side-projects.*

---

## 🧠 Smart Retention Best Practices

`KIROK_retain` feeds Kirok's own entity-extraction model (Gemini), which works best on raw, detailed text:

- **Provide raw detail, not a pre-summary.** The server's model extracts entities more reliably from full-context paragraphs than from compressed bullet points.
- **Use the `context` parameter for the source of the memory** (e.g. "project meeting", "code review"). It is stored as free text, not a fixed category.
- **The Troubleshooting Formula**: When storing fixed errors, use this exact structure in the content:
  *(1) Symptom  →  (2) Root Cause  →  (3) Fix  →  (4) Prevention*

---

## 🔍 Smart Recall Best Practices

- **Use Semantic Natural Language**: Kirok uses Hybrid Search (Vector + FTS5 + Reciprocal Rank Fusion). Provide natural language queries to `KIROK_recall` (e.g., "How did we fix the JWT token issue last week?").
- **Time Filters**: For high-volume banks, always use `time_min` and `time_max` (ISO 8601 format) to constrain the search space.

---

## 🔄 Auto-Consolidation (Smart Dedup)

Kirok features autonomous Observation Consolidation.
When you call `KIROK_retain`, the server's AI will automatically compare the new memory to existing observations. It will silently merge overlapping concepts, resolve contradictions, and generate durable "Insights".
**You don't need to manually dedup.** Just feed the facts to `KIROK_retain` and let the server handle the curation.
