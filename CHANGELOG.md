# Changelog

All notable changes to Kirok will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
