---
name: kirok
description: The ultimate, autonomous memory skill for the Kirok MCP server. Teaches the AI to proactively manage long-term memory (Retain, Recall, Reflect) without explicit user prompting. Establishes strict bank taxonomy and operational rules.
---

# Kirok: Fully Autonomous Memory Protocol

**You are equipped with Kirok (記録), the next-generation memory system.**
Kirok provides persistent, cross-session memory via its MCP tools. Your goal is to function as an autonomous "Second Brain." You must proactively manage your memory without needing the user to say "remember this."

> **Note**: Tools are prefixed with `kirok_` (e.g., `kirok_retain`, `kirok_recall`).

---

## 🛑 Core Operational Directives

You must integrate memory operations seamlessly into your standard workflow:

1. **Pre-flight Recall (The "Look Before You Leap" Rule)**
   Before starting any non-trivial task (debugging, writing a new script, planning an architecture), you MUST perform a `kirok_recall` to check for past decisions, user preferences, or known bugs.
2. **Post-Task Retain (The "Record the Lesson" Rule)**
   When a bug is fixed, a preference is stated, or a milestone is reached, you MUST instinctively execute `kirok_retain`. Do not ask the user for permission to remember important project facts.
3. **Periodic Reflection (The "Wisdom" Rule)**
   If a series of complex tasks has been completed, consider using `kirok_reflect` to synthesize underlying patterns into mental models.

---

## 🏦 Bank Taxonomy & Fragmentation Prevention

To prevent memory fragmentation (which destroys recall accuracy), you are **STRICTLY FORBIDDEN** from creating arbitrary banks. Always use one of the following standard banks:

| Bank ID | Purpose | Example Triggers |
| :--- | :--- | :--- |
| `user-prefs` | User personal preferences and rules | Coding style, language policy (e.g., Japanese for UI), workflow habits |
| `architecture` | System design and technical specs | Tool versions, specific frameworks in use, core logic choices |
| `troubleshooting` | Error logs, root causes, and fixes | "We found why X fails, we must use workaround Y." |
| `milestones` | Project achievements and work history | "Successfully migrated to Next.js on 2026-04-10" |
| `scratch` | Temporary or volatile memory | Unfinished ideas, pending tasks that don't need permanent record |

*If you absolutely must create a new bank, its purpose must be broad enough to capture at least 20 future memories. Do not create project-specific banks for tiny side-projects.*

---

## 🧠 Smart Retention Best Practices

When calling `kirok_retain`, you must optimize for Kirok's internal **Entity Extraction (Gemini) Engine**:

- **DO NOT Pre-Summarize!** Provide rich, full-context paragraphs. The Kirok server has a powerful internal LLM that works best with raw, detailed text, not compressed bullet points.
- **Categorize via Context Parameter**: Always set the `context` parameter to one of: `preference`, `knowledge`, `architecture decision`, `troubleshooting`, or `behavior`.
- **The Troubleshooting Formula**: When storing fixed errors, use this exact structure in the content:
  *(1) Symptom  →  (2) Root Cause  →  (3) Fix  →  (4) Prevention*

**Example of an excellent Retain:**
```python
kirok_retain(
    bank_id="troubleshooting",
    content="Symptom: The login API was throwing a 500. Root Cause: The JWT token was not being properly cast to a string before hashing. Fix: Added str() wrapper around token payload. Prevention: Enforce type-checking in the middle-ware.",
    context="troubleshooting"
)
```

---

## 🔍 Smart Recall Best Practices

- **Use Semantic Natural Language**: Kirok uses Hybrid Search (Vector + FTS5 + Reciprocal Rank Fusion). Provide natural language queries to `kirok_recall` (e.g., "How did we fix the JWT token issue last week?").
- **Time Filters**: For high-volume banks, always use `time_min` and `time_max` (ISO 8601 format) to constrain the search space.

---

## 🔄 Auto-Consolidation (Smart Dedup)

Kirok features autonomous Observation Consolidation.
When you call `kirok_retain`, the server's AI will automatically compare the new memory to existing observations. It will silently merge overlapping concepts, resolve contradictions, and generate durable "Insights".
**You don't need to manually dedup.** Just feed the facts to `kirok_retain` and let the server handle the curation.

---

## 📋 The "Memory Hygiene" Checklist

Before finalizing your response to the user, mentally review this checklist:
- [ ] Did the user state a preference I should store? (`user-prefs`)
- [ ] Did we solve a tricky bug that might recur? (`troubleshooting`)
- [ ] Are we embarking on a complex task without checking past history? (`kirok_recall`)

**Action:** Execute these tools autonomously as needed, perfectly managing the user's second brain.
