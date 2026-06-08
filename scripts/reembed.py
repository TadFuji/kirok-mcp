#!/usr/bin/env python3
"""Re-embed all Kirok memories and observations with task_type=RETRIEVAL_DOCUMENT.

The embedding dimension is unchanged (3072), so this is non-catastrophic: a
half-finished run leaves every row at 3072 and fully searchable, and re-running
completes the rest (re-embedding the same text yields the same vector, so a redo
is safe - it only re-spends API calls). Still, back up the DB first and stop the
running Kirok MCP server so there is no concurrent writer.

Windows / PowerShell, from the repo root using the venv python:

    # 1. Stop Kirok first (disconnect the MCP server / close the app).
    # 2. Back up the DB (WAL-aware) - required before a real run:
    #    .venv\\Scripts\\python.exe -c "import sqlite3,shutil,os; p=os.path.expanduser('~/.kirok/memory.db'); c=sqlite3.connect(p); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close(); shutil.copy(p, p+'.bak-manual')"
    # 3. Dry run (1 real API call, no DB writes) to validate the SDK + key:
    #    .venv\\Scripts\\python.exe scripts\\reembed.py --dry-run
    # 4. Real run:
    #    .venv\\Scripts\\python.exe scripts\\reembed.py

Note: keep ~/.kirok out of a cloud-sync folder while running, or pause sync -
a sync lock mid-write can corrupt the DB or spawn 'memory (1).db' conflicts.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import os
import sys
from pathlib import Path

# Make src/ importable when run directly as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from kirok_mcp.db import MemoryDB  # noqa: E402
from kirok_mcp.embeddings import (  # noqa: E402
    EMBEDDING_DIM,
    EmbeddingClient,
    TASK_TYPE_DOCUMENT,
)

EXPECTED_BYTES = EMBEDDING_DIM * 4


def _default_db_path() -> Path:
    return Path(
        os.environ.get("KIROK_DB_PATH") or (Path.home() / ".kirok" / "memory.db")
    )


def _has_backup(db_path: Path) -> bool:
    return bool(glob.glob(str(db_path) + ".bak*"))


async def _reembed_rows(embedder, rows, label, update_fn, batch_size):
    """Re-embed (id, content) rows in batches, persisting each row via update_fn."""
    total = len(rows)
    done = 0
    for start in range(0, total, batch_size):
        chunk = rows[start:start + batch_size]
        texts = [content for _id, content in chunk]
        vectors = await embedder.embed_batch(texts, task_type=TASK_TYPE_DOCUMENT)
        if len(vectors) != len(chunk):
            # RuntimeError (not SystemExit) so it propagates through the caller's
            # try/finally and the DB connection is closed cleanly.
            raise RuntimeError(
                f"{label}: embed_batch returned {len(vectors)} vectors for "
                f"{len(chunk)} inputs"
            )
        for (row_id, _content), vec in zip(chunk, vectors):
            if len(vec) != EMBEDDING_DIM:
                raise RuntimeError(
                    f"{label}: unexpected embedding length {len(vec)} "
                    f"(expected {EMBEDDING_DIM}) for id {row_id}"
                )
            update_fn(row_id, vec)
        done += len(chunk)
        print(f"  {label}: {done}/{total}")
    return total


def _verify(db: MemoryDB) -> list[str]:
    problems: list[str] = []
    bad_mem = db.conn.execute(
        "SELECT COUNT(*) FROM memories "
        "WHERE embedding IS NOT NULL AND length(embedding) != ?",
        (EXPECTED_BYTES,),
    ).fetchone()[0]
    if bad_mem:
        problems.append(f"{bad_mem} memories have a non-3072 embedding")
    bad_obs = db.conn.execute(
        "SELECT COUNT(*) FROM observations "
        "WHERE embedding IS NOT NULL AND length(embedding) != ?",
        (EXPECTED_BYTES,),
    ).fetchone()[0]
    if bad_obs:
        problems.append(f"{bad_obs} observations have a non-3072 embedding")

    if db._vec_available:
        mem_ok = db.conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE embedding IS NOT NULL AND length(embedding) = ?",
            (EXPECTED_BYTES,),
        ).fetchone()[0]
        vec_mem = db.conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
        if mem_ok != vec_mem:
            problems.append(f"vec_memories={vec_mem} != memories={mem_ok}")

        obs_ok = db.conn.execute(
            "SELECT COUNT(*) FROM observations "
            "WHERE embedding IS NOT NULL AND length(embedding) = ?",
            (EXPECTED_BYTES,),
        ).fetchone()[0]
        vec_obs = db.conn.execute(
            "SELECT COUNT(*) FROM vec_observations"
        ).fetchone()[0]
        if obs_ok != vec_obs:
            problems.append(f"vec_observations={vec_obs} != observations={obs_ok}")
    return problems


async def main_async(args: argparse.Namespace) -> int:
    if EMBEDDING_DIM != 3072:
        print(
            f"ERROR: this script expects EMBEDDING_DIM=3072 (got {EMBEDDING_DIM}). "
            "Re-embedding at a different dimension needs a different procedure.",
            file=sys.stderr,
        )
        return 1

    db_path = Path(args.db) if args.db else _default_db_path()
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY is not set (.env or environment).", file=sys.stderr)
        return 1

    if not args.dry_run:
        if not _has_backup(db_path):
            print(
                f"ERROR: no backup found next to {db_path} (expected {db_path}.bak*).\n"
                "Back up the DB first (see the header of this script), then re-run.",
                file=sys.stderr,
            )
            return 1
        if not args.yes:
            ans = input(
                "Is the Kirok MCP server stopped (no concurrent writer)? [y/N] "
            ).strip().lower()
            if ans != "y":
                print("Aborted. Stop Kirok, then re-run.", file=sys.stderr)
                return 1

    embedder = EmbeddingClient(api_key=api_key)
    db = MemoryDB(db_path=str(db_path))
    db.connect()
    try:
        mem_rows = db.conn.execute(
            "SELECT id, content FROM memories"
        ).fetchall()
        obs_rows = db.conn.execute(
            "SELECT id, content FROM observations"
        ).fetchall()
        mem_rows = [(r["id"], r["content"]) for r in mem_rows]
        obs_rows = [(r["id"], r["content"]) for r in obs_rows]
        print(
            f"DB: {db_path}\n"
            f"memories: {len(mem_rows)} | observations: {len(obs_rows)} | "
            f"vec_available: {db._vec_available}"
        )

        if args.dry_run:
            sample = (mem_rows or obs_rows)[:1]
            if not sample:
                print("Nothing to re-embed (empty DB).")
                return 0
            vec = (await embedder.embed_batch(
                [sample[0][1]], task_type=TASK_TYPE_DOCUMENT
            ))[0]
            norm = sum(x * x for x in vec) ** 0.5
            print(
                f"DRY RUN: 1 sample embedded - len={len(vec)} "
                f"(expected {EMBEDDING_DIM}), L2 norm={norm:.4f} (~=1 expected). "
                "No DB writes."
            )
            return 0 if len(vec) == EMBEDDING_DIM else 1

        await _reembed_rows(
            embedder, mem_rows, "memories", db.update_memory_embedding, args.batch
        )
        await _reembed_rows(
            embedder, obs_rows, "observations", db.update_observation_embedding, args.batch
        )

        problems = _verify(db)
        if problems:
            print("VERIFICATION FAILED:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print("Do NOT reconnect Kirok until resolved.", file=sys.stderr)
            return 1

        # Fold the WAL back into the main DB so Kirok reads the final state
        # immediately on reconnect (and a post-run backup is a single file).
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print(
            "Re-embedding complete and verified. "
            "You can now reconnect Kirok (/mcp -> kirok -> Reconnect)."
        )
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="DB path (default ~/.kirok/memory.db)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Embed one sample (real API) and report length/norm; no DB writes.",
    )
    parser.add_argument(
        "--batch", type=int, default=50, help="Embedding batch size (default 50)."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the 'Kirok stopped?' prompt. DANGEROUS: only use when you are "
        "certain Kirok is stopped - a concurrent writer can corrupt the DB.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
