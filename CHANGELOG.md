# Changelog

All notable changes to Kirok will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.1] - 2026-07-19

### Added
- Published to PyPI — install is now a one-liner (`uvx kirok-mcp`), with a Trusted-Publishing GitHub Actions workflow releasing on every GitHub Release
- `server.json` + README ownership marker for the official MCP Registry (`io.github.tadfuji/kirok-mcp`)
- The server and `kirok-doctor` also read `~/.kirok/.env`, giving pip/uvx installs (which have no project-level env file) a documented place for `GEMINI_API_KEY`

### Changed
- README quick start is PyPI-first; the git-clone flow moved to a "From source (development)" section

## [1.4.0] - 2026-07-19

### Fixed
- **A consolidation LLM failure can no longer silently retire memories.** `LLMClient.consolidate` used to swallow every error (API failure after retries, unparseable response) and return an empty action list; the consolidation engine then marked the whole batch `consolidated_at` with zero observations produced and no failure recorded — those memories were permanently skipped. It now raises: the auto path records an `auto_consolidation` failure (visible in `KIROK_stats`) and leaves the batch pending for the next run; manual `KIROK_consolidate` reports the failure and records `manual_consolidation`
- **Concurrent retains can no longer double-consolidate the same batch.** Two retains passing the debounce together could both read the same unconsolidated memories and produce near-duplicate observations. Consolidation is now serialized per bank with an `asyncio.Lock`; the second run re-reads the (now empty) pending set and exits
- **The dedup merge and its audit event now commit as one transaction.** Previously the merged content committed first and the `memory_dedup_update` audit row (holding the pre-merge content) committed separately — a crash between the two persisted the merge with no recoverable trace of the original, violating the audit guarantee
- A failure in the consolidation-debounce count query is now recorded to `system_events` (`consolidation_count`) instead of only being logged, matching every other background failure
- `scripts/search_eval.py`: `--limit 5` no longer computes hit@5 twice, and `--limit 3` no longer prints a hit@5 column that was really hit@3; its docstring now states that it measures the memory fusion layer, not the full observation-aware recall reply

### Changed
- **Observations are now reachable by keyword.** The `fts_observations` index was written on every insert/update but never queried — consolidated knowledge was only findable through the semantic floor, so an observation whose cosine landed at e.g. 0.60 was invisible even on an exact keyword match. `KIROK_recall` now runs a keyword search (BM25 + short-CJK LIKE rescue, floor-exempt like the memory side) and fuses it with the semantic hits via RRF
- **FTS text is NFKC-normalized on both the index and the query side**, so width variants now match: full-width ASCII (ＭＣＰ vs MCP) and half-width katakana (ﾊﾞｸﾞ vs バグ) were distinct code points that could never MATCH or LIKE each other. Existing databases rebuild their FTS tables once on the next startup (gated by `PRAGMA user_version`); source-table content is stored untouched
- **RRF now fuses deeper candidate pools** (`max(limit*3, 30)` per source instead of `limit`): an item ranked just outside `limit` in both sources — a classic true hit that fusion exists to promote — was previously truncated out of both lists before RRF could see it
- **The short-CJK LIKE rescue is now OR-joined across tokens**, matching the OR-joined FTS MATCH semantics. Previously it required every short token to appear (`京都 大阪` matched only rows containing both), while 3+ char queries used OR — an inconsistency that hid partial matches
- `vec_search` no longer fetches and deserializes each hit's embedding BLOB (top_k × 3072 floats per call) — no consumer of its results reads it
- New partial index `idx_memories_unconsolidated` makes the per-retain consolidation-debounce count and `get_unconsolidated_memories` index-only instead of scanning the bank's rows
- `set_bank_config` uses an upsert (`ON CONFLICT DO UPDATE`) instead of SELECT-then-INSERT, removing a cross-process race on first config write
- `KIROK_retain`'s dedup-UPDATE path now runs its re-extraction and re-embedding concurrently (`asyncio.gather`), like the ADD path already did

### Removed
- `MemoryDB.search_by_timestamp` — dead code since 1.3.0 made time-filtered recall filter FTS hits directly; nothing called it

## [1.3.0] - 2026-07-10

### Changed
- **Short Japanese keyword queries now work**: 1-2 character kanji/katakana tokens (京都, 会議, バグ — below the trigram tokenizer's 3-char window, so they could never MATCH) are now served by an exact-substring LIKE supplement over the FTS text, appended after the BM25-ranked hits. Hiragana-only short tokens stay excluded (function words would substring-match half the bank). Previously these queries silently degraded hybrid search to semantic-only. The rescue now also runs whenever FTS already filled the results with 3+ char matches, instead of only when FTS came back short — a genuine short-token hit could otherwise be dropped
- `KIROK_clear_bank` and `KIROK_delete_bank` now require `confirm=true`. Without it they change nothing and return a preview of what would be deleted — a single mistaken tool call can no longer wipe a bank
- **FTS5 multi-word queries now use OR instead of implicit AND**: a document matching any query token is a hit, with BM25 naturally ranking documents that match more tokens higher — instead of requiring every token to match, which silently hid partial keyword overlaps
- FTS hits now carry `timestamp` and `entities` (joined from `memories`), fixing the `[unknown]` placeholder that FTS-only recall results used to show and letting time-filtered recall filter FTS hits by their real timestamp directly, instead of intersecting with a separate `search_by_timestamp` call capped at `limit * 2` — which could silently drop in-range but relatively old FTS hits
- RRF merging (`reciprocal_rank_fusion`) now merges matching items field-by-field instead of letting one list's dict fully overwrite the other's, so an FTS-only hit keeps its semantic metadata and vice versa
- `vec_search`'s unused `candidate_multiplier` parameter is removed (per-bank KNN needs no over-fetch window; it stopped affecting results after the 1.1.0 per-bank partitioning)
- `KIROK_retain`'s embedding and entity-extraction calls now run concurrently (`asyncio.gather`) instead of sequentially, cutting retain latency
- `KIROK_recall` no longer lists an observation's own source memories under "Supporting Memories" — they were duplicated there and double-counted against `limit`
- `kirok-doctor` gains an `--online` flag that adds one live embedding call to verify Gemini connectivity; default (offline) behavior is unchanged

### Added
- `scripts/search_eval.py` (+ `search_eval.example.json`): measures recall quality (hit@1/5/k, MRR) against a golden query set, through the exact recall pipeline the server uses (extracted as `hybrid_search_memories`). This is the yardstick for tuning search parameters — before it, no search change could be shown to help or hurt
- SQLite connections now set `PRAGMA busy_timeout=30000` (30s), so concurrent MCP client instances wait out a busy writer instead of failing immediately with `database is locked`
- Consolidation is now atomic: every create/update embedding is generated before any database write, and all observation changes plus `mark_memories_consolidated` commit as a single transaction. A failure (embedding or DB) leaves the database exactly as it was, with the source memories still unconsolidated for a later retry — instead of a partially-applied batch
- Observations are now soft-deleted: consolidation's LLM-decided deletes stamp a new `observations.deprecated_at` column instead of removing the row (idempotent migration for existing databases). Deprecated observations are excluded from search, listing, and stats, but the row and an `observation_deprecated` audit event (in `system_events`) survive for recovery
- Startup auto-snapshot: on server startup, a `VACUUM INTO` snapshot of the live database is taken to `~/.kirok/backups/memory-auto-*.db` if the newest existing auto-snapshot is older than `KIROK_AUTO_SNAPSHOT_HOURS` (default 24; `0` disables it), rotating to keep the newest `KIROK_SNAPSHOT_KEEP` (default 5). Only auto-snapshot files are ever rotated — manual `kirok-backup snapshot`/`export` files are untouched
- A snapshot that fails partway (VACUUM INTO error or integrity check failure) no longer leaves a broken output file behind
- JSON export/import now reads every table inside a single transaction (consistent cross-table snapshot even under concurrent WAL writers) and carries `deprecated_at` for observations; importing an older export without that field still works
- A similarity floor for recall: `KIROK_RECALL_MIN_SIMILARITY` (default `0.62`) drops semantic (vector) memory hits below this cosine similarity, and `KIROK_OBS_MIN_SIMILARITY` (default `0.62`, replacing a hardcoded `0.4` that was below even off-topic scores) does the same for observations. FTS/keyword hits are exempt — a literal term match is independent evidence. 0.62 is calibrated on live `gemini-embedding-001` data: off-topic queries score 0.55-0.62 against unrelated banks, true hits score 0.66-0.73
- Embedding calls now guard against dimension drift: a returned vector whose length doesn't match `EMBEDDING_DIM` (3072) raises `ValueError` instead of being silently stored
- `KIROK_stats` now reports `API calls this session: embeddings=N, llm=M`, a session-lifetime counter on the embedding/LLM clients (resets on restart)
- New offline tests: `test_llm_retry.py`, `test_search_eval.py`, `test_consolidation_atomicity.py`, `test_search_quality.py`, `test_stage3.py`, short-CJK rescue and confirm-guard cases

### Fixed
- `consolidate`, `evaluate_importance`, and `deduplicate` now actually retry transient Gemini failures (5xx/429/network). The 1.2.0 notes claimed all LLM calls retried, but these three fell straight to their fail-open defaults on a single transient error — e.g. a duplicate memory stored because deduplication "failed"
- Deduplication's UPDATE path now records the pre-merge content as a `memory_dedup_update` audit event before overwriting it, so a bad LLM merge can be reconstructed instead of silently erasing the original memory
- `observation_deprecated` and `memory_dedup_update` audit events no longer show up in `KIROK_stats`'s background-failure list — they are an audit trail, not failures, and were crowding out real background failures

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
