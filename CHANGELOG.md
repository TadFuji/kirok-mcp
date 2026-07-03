# Changelog

All notable changes to Kirok will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Short Japanese keyword queries now work**: 1-2 character kanji/katakana tokens (京都, 会議, バグ — below the trigram tokenizer's 3-char window, so they could never MATCH) are now served by an exact-substring LIKE supplement over the FTS text, appended after the BM25-ranked hits. Hiragana-only short tokens stay excluded (function words would substring-match half the bank). Previously these queries silently degraded hybrid search to semantic-only
- `KIROK_clear_bank` and `KIROK_delete_bank` now require `confirm=true`. Without it they change nothing and return a preview of what would be deleted — a single mistaken tool call can no longer wipe a bank

### Added
- `scripts/search_eval.py` (+ `search_eval.example.json`): measures recall quality (hit@1/5/k, MRR) against a golden query set, through the exact recall pipeline the server uses (extracted as `hybrid_search_memories`). This is the yardstick for tuning search parameters — before it, no search change could be shown to help or hurt
- New offline tests: `test_llm_retry.py`, `test_search_eval.py`, short-CJK rescue and confirm-guard cases

### Fixed
- `consolidate`, `evaluate_importance`, and `deduplicate` now actually retry transient Gemini failures (5xx/429/network). The 1.2.0 notes claimed all LLM calls retried, but these three fell straight to their fail-open defaults on a single transient error — e.g. a duplicate memory stored because deduplication "failed"

## [1.2.0] - 2026-07-03

### Changed
- Importing `kirok_mcp.server` is now side-effect-free: the database connection and Gemini clients are created at server startup (`main()`), and the `GEMINI_API_KEY` check moved from import time to startup — importing the module (tests, tooling) no longer opens the database or exits the process
- CI no longer needs a dummy `GEMINI_API_KEY`
- `db.py` (2000 lines) is now the `kirok_mcp.db` package, split by domain (schema, memories, search, observations, models, banks) and composed into the unchanged `MemoryDB` facade; the import surface is identical
- The six copies of the vec-index sync (delete-then-insert with size guard) are consolidated into one `_sync_vec_row` helper
- `KIROK_stats` now reports recent background failures (see Added)
- Embedding and LLM calls now use the async Gemini client (`client.aio`) instead of blocking calls, so concurrent requests overlap and `KIROK_REFLECT_TIMEOUT` / `KIROK_CONSOLIDATION_TIMEOUT` actually take effect (they previously could not interrupt a blocking SDK call)
- Auto-consolidation is now debounced: it runs once at least `KIROK_CONSOLIDATION_BATCH_SIZE` (default 5) memories are pending, instead of after every retain — cutting `KIROK_retain` latency and Gemini token usage. The consolidation report is no longer appended to the retain reply
- `KIROK_recall` output is compact by default (content + ID only); pass `verbose=true` to include RRF/Sim relevance scores
- **Japanese keyword search**: FTS5 now uses the `trigram` tokenizer instead of the default (which split CJK into single characters, leaving BM25 ineffective for Japanese). Existing databases rebuild their FTS index automatically on the next connect (rebuilt from the source tables, in a transaction that rolls back on failure). Queries shorter than 3 characters are served by semantic search instead (trigram cannot index them)
- **Embeddings use asymmetric task types**: stored content uses `RETRIEVAL_DOCUMENT`, search queries use `RETRIEVAL_QUERY`, improving retrieval relevance. Dimension stays 3072 (pre-normalized). Existing data must be re-embedded once via `scripts/reembed.py` to gain the benefit
- Observation recall now uses a per-bank sqlite-vec `vec0` KNN (`vec_observations`) with transparent brute-force fallback, mirroring memory search
- Time-range `KIROK_recall` now filters by timestamp in SQL before scoring, instead of loading the whole bank into memory
- `KIROK_stats` counts observations and unconsolidated memories via `COUNT(*)` (accurate beyond 1000 rows; previously capped)
- `KIROK_update_memory` now re-extracts entities/keywords under the bank's `retain_mission`, consistent with the retain pipeline
- Gemini embedding and LLM calls retry transient failures (5xx, 429, network) with bounded exponential backoff; structured errors (auth/bad-request) still fail fast
- `semantic_search` is vectorized (single numpy matrix op) for faster brute-force/observation/time-range scoring

### Added
- Background failures (auto-consolidation, mental-model auto-refresh) are now recorded in a capped `system_events` table and surfaced by `KIROK_stats`, instead of being visible only in server logs
- `kirok-backup` command: `snapshot` (verified `VACUUM INTO` copy of the live database), `export` (portable JSON of all banks, embeddings included), and `import` (transactional restore that skips existing IDs and rebuilds FTS/vector indexes). Fully offline — no API key required
- `KIROK_CONSOLIDATION_BATCH_SIZE` environment variable (default 5) to tune auto-consolidation debouncing (set to 1 to restore per-retain consolidation)
- `verbose` parameter on `KIROK_recall`
- `MemoryDB.count_unconsolidated_memories` helper
- `scripts/reembed.py`: idempotent, resumable re-embedding of all memories and observations (with `--dry-run`, backup-required gate, and a post-run integrity check)
- `kirok-doctor` now checks that the sqlite-vec extension loads (the actual KNN backend), not just FTS5
- `vec_observations` table plus `MemoryDB.vec_search_observations`, `get_embeddings_in_range`, `update_memory_embedding`, and `update_observation_embedding` helpers
- New offline tests: `test_retry.py`, `test_fts_trigram.py`, `test_server_startup.py` (import is side-effect-free; missing API key fails at startup), and observation-vector / task-type / stats cases

### Fixed
- `KIROK_smart_retain` now honors `threshold` values below 5. Previously a hardcoded `score >= 5` floor in importance evaluation silently overrode lower thresholds, so `threshold=3` could never retain a score-3/4 item (e.g. a subtle preference)
- `clear_bank`, `delete_bank`, `delete_observation`, and `clear_observations` now keep the observation vector index in sync

## [1.1.0] - 2026-06-06

### Added
- Per-bank sqlite-vec `vec0` KNN for memory semantic search, with transparent fallback to brute-force cosine when the native extension is unavailable
- `sqlite-vec` runtime dependency, an `EMBEDDING_DIM = 3072` constant, and a startup migration that backfills and reconciles the `vec_memories` table from stored embeddings
- Offline setup diagnostics via the new `kirok-doctor` command
- `auto_refresh` and `source_query` options for `KIROK_reflect` mental models
- Test suite covering db, server, diagnostics, embeddings, and vector search (`test_vec_*`); `pytest` dev dependency

### Changed
- `KIROK_smart_retain` now routes through the shared retain pipeline
- Bank clear/delete logic refactored to share a `_delete_bank_data` helper

### Fixed
- Clearing or deleting a bank now consistently removes its observations, FTS index rows, and bank configuration

## [1.0.0] - 2026-04-09

### Added
- **Core Memory Operations**: Retain, Recall, Reflect — the three pillars of agent memory
- **Smart Deduplication**: Mem0-inspired ADD/UPDATE/NOOP pipeline with configurable similarity threshold
- **Hybrid Search**: Semantic (cosine similarity) + Keyword (FTS5 BM25) merged via Reciprocal Rank Fusion
- **Observation Consolidation**: Autonomous pattern extraction from accumulated memories
- **Mental Models**: LLM-generated insights with optional auto-refresh
- **Smart Retain**: Importance-scored ingestion for bulk/automatic content
- **Bank Configuration**: Per-bank retain and observations missions
- **19 MCP tools**: Full CRUD for memories, mental models, observations, and bank management
- **FTS5 query sanitization**: Safe handling of special characters in search queries
- **MIT License**: Open-source under the MIT License
