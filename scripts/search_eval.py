#!/usr/bin/env python3
"""Measure Kirok recall quality against a golden query set.

Runs each golden query through the REAL recall pipeline (the same
``hybrid_search_memories`` the MCP server uses — semantic KNN + FTS keyword
search merged with RRF) and reports hit@k and MRR. This is the yardstick for
tuning search parameters (dedup threshold, RRF k, FTS behavior): without it,
no change to search can be shown to help or hurt.

Golden set format (JSON, UTF-8):

    {
      "cases": [
        {
          "bank_id": "work-history",
          "query": "uv sync が失敗する問題",
          "expected_ids": ["<memory uuid>"],
          "expected_substrings": ["uv sync"],
          "note": "optional human note"
        }
      ]
    }

A result counts as a hit if its id is in ``expected_ids`` OR its content
contains any of ``expected_substrings`` (either list may be omitted).
Substrings are the robust choice — they survive re-imports that change IDs.

Usage (repo root, venv python; needs GEMINI_API_KEY via .env for query
embeddings — one API call per case):

    .venv\\Scripts\\python.exe scripts\\search_eval.py scripts\\search_eval.example.json
    .venv\\Scripts\\python.exe scripts\\search_eval.py my_golden.json --limit 10

The database is only read (searches), but connecting runs Kirok's idempotent
schema migrations like any other client. Uses KIROK_DB_PATH or
~/.kirok/memory.db; override with --db.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Make src/ importable when run directly as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from kirok_mcp.db import MemoryDB  # noqa: E402
from kirok_mcp.embeddings import EmbeddingClient  # noqa: E402
from kirok_mcp.server import hybrid_search_memories  # noqa: E402


def _default_db_path() -> Path:
    return Path(
        os.environ.get("KIROK_DB_PATH") or (Path.home() / ".kirok" / "memory.db")
    )


def first_hit_rank(
    results: list[dict],
    expected_ids: list[str],
    expected_substrings: list[str],
) -> int | None:
    """1-based rank of the first result matching the expectation, or None."""
    id_set = set(expected_ids)
    for rank, item in enumerate(results, start=1):
        if item.get("id") in id_set:
            return rank
        content = item.get("content", "")
        if any(s in content for s in expected_substrings):
            return rank
    return None


def summarize(ranks: list[int | None], ks: tuple[int, ...] = (1, 5, 10)) -> dict:
    """Aggregate per-case ranks into hit@k rates and MRR (all in [0, 1])."""
    n = len(ranks)
    if n == 0:
        return {"cases": 0, "mrr": 0.0, **{f"hit@{k}": 0.0 for k in ks}}
    summary = {"cases": n}
    for k in ks:
        hits = sum(1 for r in ranks if r is not None and r <= k)
        summary[f"hit@{k}"] = hits / n
    summary["mrr"] = sum(1.0 / r for r in ranks if r is not None) / n
    return summary


async def run_cases(db, embedder, cases: list[dict], limit: int) -> list[dict]:
    """Run each golden case through the real recall pipeline.

    Returns one dict per case: the case itself plus ``rank`` (1-based, or
    None for a miss) and ``top_id`` (the #1 result, for miss diagnosis).
    """
    outcomes = []
    for case in cases:
        results = await hybrid_search_memories(
            db, embedder, case["bank_id"], case["query"], limit
        )
        rank = first_hit_rank(
            results,
            case.get("expected_ids", []),
            case.get("expected_substrings", []),
        )
        outcomes.append({
            **case,
            "rank": rank,
            "top_id": results[0]["id"] if results else None,
        })
    return outcomes


def _load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    for i, case in enumerate(cases):
        if "bank_id" not in case or "query" not in case:
            sys.exit(f"ERROR: case {i} is missing 'bank_id' or 'query'")
        if not case.get("expected_ids") and not case.get("expected_substrings"):
            sys.exit(
                f"ERROR: case {i} ('{case['query']}') needs "
                "'expected_ids' or 'expected_substrings'"
            )
    if not cases:
        sys.exit("ERROR: golden set has no cases")
    return cases


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("golden", type=Path, help="Golden set JSON file")
    parser.add_argument("--limit", type=int, default=10, help="Recall depth (default 10)")
    parser.add_argument("--db", type=Path, default=None, help="Database path override")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        sys.exit("ERROR: GEMINI_API_KEY is required (query embeddings).")

    cases = _load_cases(args.golden)
    db_path = args.db or _default_db_path()
    if not db_path.exists():
        sys.exit(f"ERROR: database not found: {db_path}")

    db = MemoryDB(db_path=db_path)
    db.connect()
    try:
        embedder = EmbeddingClient(api_key=api_key)
        outcomes = await run_cases(db, embedder, cases, args.limit)
    finally:
        db.close()

    print(f"Golden set: {args.golden} ({len(outcomes)} cases, limit={args.limit})")
    print(f"Database:   {db_path}\n")
    for o in outcomes:
        mark = f"rank {o['rank']}" if o["rank"] is not None else "MISS"
        print(f"  [{mark:>7}] ({o['bank_id']}) {o['query']}")
        if o["rank"] is None:
            print(f"            top result was: {o['top_id']}")

    summary = summarize([o["rank"] for o in outcomes], ks=(1, 5, args.limit))
    print(
        f"\nhit@1: {summary['hit@1']:.2f}  "
        f"hit@5: {summary['hit@5']:.2f}  "
        f"hit@{args.limit}: {summary[f'hit@{args.limit}']:.2f}  "
        f"MRR: {summary['mrr']:.3f}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
