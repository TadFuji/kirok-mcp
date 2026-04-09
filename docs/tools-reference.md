# Tools Reference

Complete documentation for all 17 Kiroku MCP tools.

---

## Core Operations

### `kiroku_retain`

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
kiroku_retain(
    bank_id="my-project",
    content="The deploy pipeline uses GitHub Actions with a staging environment on Vercel",
    context="architecture decision"
)
```

---

### `kiroku_recall`

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
kiroku_recall(
    bank_id="my-project",
    query="deployment pipeline",
    limit=5
)
```

---

### `kiroku_reflect`

Generate insights from accumulated memories.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Memory bank to reflect on |
| `query` | string | ✅ | — | Topic, question, or prompt |
| `limit` | int | ❌ | `20` | Max memories to analyze (1-100) |

**Behavior:**
1. Retrieves relevant memories via semantic search
2. Sends to LLM with existing mental models
3. Saves result as a new mental model

**Example:**
```
kiroku_reflect(
    bank_id="my-project",
    query="What architectural patterns have emerged in this project?"
)
```

---

### `kiroku_smart_retain`

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
- Only stores if score >= threshold
- Ideal for bulk/automatic ingestion

---

### `kiroku_consolidate`

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

### `kiroku_get_memory`

Get full details of a specific memory.

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `memory_id` | string | ✅ | The memory UUID |

### `kiroku_update_memory`

Update content and/or context. Re-extracts entities and regenerates embedding if content changes.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `memory_id` | string | ✅ | — | Memory to update |
| `content` | string | ❌ | `""` | New content (empty = keep current) |
| `context` | string | ❌ | `""` | New context (empty = keep current) |

### `kiroku_forget`

Delete a specific memory. **Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `memory_id` | string | ✅ | Memory to delete |

### `kiroku_list_memories`

Browse memories with pagination.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Bank to browse |
| `limit` | int | ❌ | `20` | Results per page (1-100) |
| `offset` | int | ❌ | `0` | Skip N memories |

---

## Mental Models

### `kiroku_list_mental_models`

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `bank_id` | string | ✅ | — | Bank to list models from |
| `limit` | int | ❌ | `10` | Max models to return |

### `kiroku_get_mental_model`

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `model_id` | string | ✅ | Mental model UUID |

### `kiroku_delete_mental_model`

**Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `model_id` | string | ✅ | Mental model to delete |

### `kiroku_refresh_mental_model`

Re-analyze with latest memories.

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `model_id` | string | ✅ | — | Model to refresh |
| `limit` | int | ❌ | `20` | Max memories to consider (1-100) |

---

## Bank Management

### `kiroku_list_banks`

List all memory banks with counts. No parameters.

### `kiroku_stats`

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to get stats for |

Returns: memory count, mental model count, observation count, unconsolidated count.

### `kiroku_clear_bank`

Delete all memories. Mental models preserved. **Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to clear |

### `kiroku_delete_bank`

Delete bank + all memories + all mental models. **Irreversible.**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to delete |

---

## Configuration

### `kiroku_set_bank_config`

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

### `kiroku_get_bank_config`

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bank_id` | string | ✅ | Bank to query |
