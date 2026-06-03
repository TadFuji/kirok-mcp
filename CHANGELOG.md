# Changelog

All notable changes to Kirok will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Offline setup diagnostics via the new `kirok-doctor` command
- `auto_refresh` and `source_query` options for `KIROK_reflect` mental models
- Initial unit test suite (`test_db`, `test_server`, `test_diagnostics`, `test_embeddings`)

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
