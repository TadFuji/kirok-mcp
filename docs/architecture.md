# Architecture

This document describes the internal architecture of Kirok, a persistent memory system for AI agents.

## System Overview

Kirok is a **Model Context Protocol (MCP) server** that provides AI agents with persistent, searchable memory. It runs as a local process and communicates with MCP clients via the stdio transport.

```
MCP Client (Claude, Antigravity, etc.)
    ↕ stdin/stdout (JSON-RPC 2.0)
Kirok MCP Server (FastMCP)
    ↕
SQLite Database (~/.kirok/memory.db)
    ↕ (API calls for embeddings & LLM)
Google Gemini API
```

## Core Components

### 1. Server (`server.py`)

The main entry point. Built on [FastMCP](https://github.com/jlowin/fastmcp), it:
- Registers 19 MCP tools
- Initializes database, embedding, and LLM clients as module-level singletons
- Manages the Retain-Recall-Reflect lifecycle
- Orchestrates observation consolidation

### 2. Database (`db.py`)

SQLite-backed storage with FTS5 full-text search. Key design decisions:
- **WAL mode** for concurrent read/write performance
- **Binary BLOB storage** for embedding vectors (packed float32 arrays)
- **FTS5 virtual tables** for keyword search with BM25 ranking
- **Automatic schema migration** for forward compatibility

#### Schema

```sql
-- Core memory storage
memories (
    id TEXT PRIMARY KEY,           -- UUID
    bank_id TEXT NOT NULL,         -- Logical partition
    content TEXT NOT NULL,         -- The memory text
    entities TEXT DEFAULT '[]',    -- JSON array of extracted entities
    keywords TEXT DEFAULT '[]',   -- JSON array of extracted keywords
    context TEXT DEFAULT '',      -- Source context
    embedding BLOB,              -- Binary float32 vector
    timestamp TEXT NOT NULL,      -- User-provided or auto-generated
    created_at TEXT NOT NULL,     -- System timestamp
    metadata TEXT DEFAULT '{}',  -- Extensible JSON metadata
    consolidated_at TEXT         -- NULL until processed by consolidation
)

-- Consolidated patterns and durable knowledge
observations (
    id TEXT PRIMARY KEY,
    bank_id TEXT NOT NULL,
    content TEXT NOT NULL,
    source_memory_ids TEXT DEFAULT '[]',
    embedding BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- LLM-generated insights
mental_models (
    id TEXT PRIMARY KEY,
    bank_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    insight TEXT NOT NULL,
    based_on TEXT DEFAULT '[]',
    auto_refresh INTEGER DEFAULT 0,
    source_query TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- Per-bank configuration
bank_config (
    bank_id TEXT PRIMARY KEY,
    retain_mission TEXT DEFAULT '',
    observations_mission TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)

-- sqlite-vec KNN index for memory semantic search.
-- Created only when the sqlite-vec extension loads; rebuilt/reconciled from
-- `memories` on startup. bank_id is a vec0 PARTITION KEY so KNN is scoped
-- per-bank. If the extension can't load, this table is absent and search
-- falls back to brute-force cosine.
vec_memories USING vec0 (
    memory_id TEXT PRIMARY KEY,
    bank_id TEXT PARTITION KEY,
    embedding float[3072] distance_metric=cosine
)
```

### 3. Embeddings (`embeddings.py`)

Handles vector operations:
- **Model**: `gemini-embedding-001` — 3072-dimensional output, 2048 max input tokens, 100+ languages
- **Cosine similarity**: NumPy-based computation for ranking and the brute-force path
- **Semantic search**: per-bank sqlite-vec `vec0` cosine KNN for memories (transparent brute-force fallback); brute-force cosine for observations
- **Reciprocal Rank Fusion**: Merges semantic and keyword rankings

#### Why sqlite-vec with a brute-force fallback?

Memory semantic search runs as a per-bank KNN over a sqlite-vec `vec0` virtual
table (`vec_memories`). `bank_id` is a vec0 **partition key**, so the KNN is
scoped to a single bank in SQL — a large bank can never crowd a smaller bank out
of the results (a hazard of any global-KNN + post-filter approach).

If the native `sqlite-vec` extension cannot load (for example, a Python build
without extension support), search transparently falls back to exact brute-force
cosine over the bank — same results, only slower. Brute-force is also used for
the observation layer and for time-filtered recall (vec0 cannot filter by
timestamp). For the typical hundreds-to-thousands of memories per bank both
paths are fast; the vec index keeps recall cheap as banks grow.

### 4. LLM Client (`llm.py`)

Gemini Flash Lite for lightweight LLM tasks:
- **Entity extraction**: Structured extraction of people, places, organizations, concepts
- **Reflection**: Multi-memory analysis to generate insights
- **Consolidation**: Pattern recognition across unconsolidated memories
- **Importance scoring**: 1-10 evaluation for smart retain
- **Deduplication**: Mem0-inspired ADD/UPDATE/NOOP decisions

## Data Flow

### Retain Flow

```
Input: content + context
    │
    ▼
┌─────────────────┐
│ Generate Embedding │ ← gemini-embedding-001
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Find Similar     │ ← vec_search (cosine > 0.85)
│ Memories         │
└────────┬────────┘
         │
    ┌────┴────┐
    │ Similar? │
    └────┬────┘
     Yes │        No
    ┌────▼────┐  ┌────▼────┐
    │ Dedup   │  │ Extract │ ← gemini-2.5-flash-lite
    │ Decision│  │ Entities│
    └────┬────┘  └────┬────┘
         │            │
  ┌──────┼──────┐     │
  ADD  UPDATE  NOOP   │
  │      │      │     │
  ▼      ▼      ▼     ▼
┌───────────────────────┐
│ SQLite + FTS5 Index    │
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│ Auto-Consolidation     │
│ (Observations)         │
└────────────────────────┘
```

### Recall Flow

```
Input: query + optional time range
    │
    ▼
┌──────────────────────┐     ┌──────────────────────┐
│ Semantic Search       │     │ FTS5 Keyword Search   │
│ (sqlite-vec KNN)      │     │ (BM25 ranking)        │
└──────────┬───────────┘     └──────────┬───────────┘
           │                            │
           └───────────┬────────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Reciprocal Rank │
              │ Fusion (k=60)   │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Observations    │ ← Consolidated knowledge first
              │ + Memories      │ ← Then supporting evidence
              └─────────────────┘
```

## Design Principles

1. **Local-First**: All data stored locally in SQLite. No cloud storage dependency.
2. **Fail-Open**: LLM failures default to retaining information (better to have duplicates than lose memories).
3. **Zero Configuration**: Works out of the box with just an API key. Missions and tuning are optional.
4. **Transparent**: Every operation returns detailed results showing what happened and why.
5. **MCP Native**: Built for the Model Context Protocol from the ground up.
