# Tools Reference

Complete documentation for all 19 Kirok MCP tools.

---

## Core Operations

### `KIROK_retain`

Store new information in agent memory.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Memory bank identifier (e.g. `"project-alpha"`, `"user-prefs"`) |
| `content` | string | ✅ | — | The information to remember |
| `context` | string | ❌ | `""` | Source context (e.g. `"meeting notes"`, `"code review"`) |
| `timestamp` | string | ❌ | now | ISO 8601 timestamp |

**Behavior:**
1. Generates embedding via Gemini
2. Checks for duplicates (cosine > 0.85 threshold)
3. If duplicate found: ADD / UPDATE / NOOP decision via LLM
4. Extracts entities and keywords
5. Stores in SQLite + FTS5 index
6. Triggers auto-consolidation

**Example:**
```
KIROK_retain(
    bank_id="my-project",
    content="The deploy pipeline uses GitHub Actions with a staging environment on Vercel",
    context="architecture decision"
)
```

---

### `KIROK_recall`

Search and retrieve relevant memories.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Memory bank to search |
| `query` | string | ✅ | — | Natural language search query |
| `limit` | int | ❌ | `10` | Max results (1-50) |
| `time_min` | string | ❌ | `""` | ISO 8601 lower bound |
| `time_max` | string | ❌ | `""` | ISO 8601 upper bound |

**Behavior:**
1. Runs semantic search (cosine similarity)
2. Runs keyword search (FTS5 BM25)
3. Merges via Reciprocal Rank Fusion (k=60)
4. Shows observations first, then memories

**Example:**
```
KIROK_recall(
    bank_id="my-project",
    query="deployment pipeline",
    limit=5
)
```

---

### `KIROK_reflect`

Generate insights from accumulated memories.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Memory bank to reflect on |
| `query` | string | ✅ | — | Topic, question, or prompt |
| `limit` | int | ❌ | `20` | Max memories to analyze (1-100) |
| `auto_refresh` | bool | ❌ | `false` | Refresh this mental model after future consolidations |
| `source_query` | string | ❌ | `""` | Query to use for future refreshes (defaults to `query`) |

**Behavior:**
1. Retrieves relevant memories via semantic search
2. Sends to LLM with existing mental models
3. Saves result as a new mental model
4. If `auto_refresh=true`, future consolidation will refresh it using `source_query`

**Example:**
```
KIROK_reflect(
    bank_id="my-project",
    query="What architectural patterns have emerged in this project?",
    auto_refresh=True
)
```

---

### `KIROK_smart_retain`

Evaluate importance before storing.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Memory bank identifier |
| `content` | string | ✅ | — | Content to evaluate |
| `context` | string | ❌ | `""` | Source context |
| `timestamp` | string | ❌ | now | ISO 8601 timestamp |
| `threshold` | int | ❌ | `5` | Minimum score to retain (1-10) |

**Behavior:**
- LLM scores content 1-10
- Only proceeds if score >= threshold
- Passing content uses the same retain pipeline as `KIROK_retain`
- Deduplication, memory updates, NOOP decisions, and auto-consolidation apply
- Ideal for bulk/automatic ingestion

---

### `KIROK_consolidate`

Manually trigger observation consolidation.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Memory bank to consolidate |

**Behavior:**
- Finds unconsolidated memories
- Creates/updates/deletes observations
- Auto-refreshes mental models with `auto_refresh=True`

---

## Memory Management

### `KIROK_get_memory`

Get full details of a specific memory.

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `memory_id` | string | ✅ | The memory UUID |

### `KIROK_update_memory`

Update content and/or context. Re-extracts entities and regenerates embedding if content changes.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `memory_id` | string | ✅ | — | Memory to update |
| `content` | string | ❌ | `""` | New content (empty = keep current) |
| `context` | string | ❌ | `""` | New context (empty = keep current) |

### `KIROK_forget`

Delete a specific memory. **Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `memory_id` | string | ✅ | Memory to delete |

### `KIROK_list_memories`

Browse memories with pagination.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Bank to browse |
| `limit` | int | ❌ | `20` | Results per page (1-100) |
| `offset` | int | ❌ | `0` | Skip N memories |

---

## Mental Models

### `KIROK_list_mental_models`

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Bank to list models from |
| `limit` | int | ❌ | `10` | Max models to return |

### `KIROK_get_mental_model`

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `model_id` | string | ✅ | Mental model UUID |

### `KIROK_delete_mental_model`

**Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `model_id` | string | ✅ | Mental model to delete |

### `KIROK_refresh_mental_model`

Re-analyze with latest memories.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `model_id` | string | ✅ | — | Model to refresh |
| `limit` | int | ❌ | `20` | Max memories to consider (1-100) |

---

## Bank Management

### `KIROK_list_banks`

List all memory banks with counts. No parameters.

### `KIROK_stats`

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to get stats for |

Returns: memory count, mental model count, observation count, unconsolidated count.

### `KIROK_clear_bank`

Delete all memories and observations. Mental models and bank configuration are preserved. **Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to clear |

### `KIROK_delete_bank`

Delete bank + all memories + observations + mental models + bank configuration. **Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to delete |

---

## Configuration

### `KIROK_set_bank_config`

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Bank to configure |
| `retain_mission` | string | ❌ | `""` | Guides entity/keyword extraction |
| `observations_mission` | string | ❌ | `""` | Guides pattern consolidation |

**Example missions:**
```
retain_mission: "Focus on architecture decisions, technology choices, and team preferences. Ignore routine status updates."

observations_mission: "Synthesize patterns about deployment frequency, preferred tools, and recurring pain points."
```

### `KIROK_get_bank_config`

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to query |
