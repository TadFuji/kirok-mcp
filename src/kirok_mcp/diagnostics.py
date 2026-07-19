"""Offline diagnostics for Kirok setup and runtime prerequisites."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv


MIN_PYTHON = (3, 12)


@dataclass
class DiagnosticResult:
    """Result for one diagnostic check."""

    name: str
    status: str
    message: str


def _project_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _env_path() -> Path:
    """The .env the server would use: dev-checkout first, else ~/.kirok/.env.

    Mirrors server.py's load order — a dev checkout keeps its key next to
    pyproject.toml; an installed package (pip/uvx) reads ~/.kirok/.env.
    """
    project = _project_dir() / ".env"
    if project.exists():
        return project
    return Path.home() / ".kirok" / ".env"


def _resolve_db_path(raw_path: str | None = None) -> Path:
    if raw_path:
        return Path(raw_path)
    return Path.home() / ".kirok" / "memory.db"


def _check_python_version() -> DiagnosticResult:
    version = sys.version_info
    current = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) >= MIN_PYTHON:
        return DiagnosticResult(
            "python_version",
            "pass",
            f"Python {current} satisfies >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}.",
        )
    return DiagnosticResult(
        "python_version",
        "fail",
        f"Python {current} is too old; install Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}.",
    )


def _check_env_file(env_path: Path | None = None) -> DiagnosticResult:
    path = env_path or _env_path()
    if path.exists():
        return DiagnosticResult("env_file", "pass", f"Found .env at {path}.")

    example = path.with_name(".env.example")
    if example.exists():
        return DiagnosticResult(
            "env_file",
            "warn",
            f"No .env found at {path}. Copy .env.example to .env and set GEMINI_API_KEY.",
        )
    return DiagnosticResult(
        "env_file",
        "warn",
        f"No .env found at {path}, and .env.example was not found beside it.",
    )


def _check_api_key(env: dict[str, str] | None = None) -> DiagnosticResult:
    env_map = os.environ if env is None else env
    key = env_map.get("GEMINI_API_KEY", "")
    if key.strip():
        return DiagnosticResult(
            "gemini_api_key",
            "pass",
            "GEMINI_API_KEY is set. Value hidden.",
        )
    return DiagnosticResult(
        "gemini_api_key",
        "fail",
        "GEMINI_API_KEY is not set. Add it to .env or the process environment.",
    )


def _check_dependency(module_name: str) -> DiagnosticResult:
    if importlib.util.find_spec(module_name) is not None:
        return DiagnosticResult(
            f"dependency_{module_name}",
            "pass",
            f"Python module '{module_name}' is importable.",
        )
    return DiagnosticResult(
        f"dependency_{module_name}",
        "fail",
        f"Python module '{module_name}' is not importable. Run 'uv sync'.",
    )


def _check_sqlite_fts5(
    connect: Callable[[str], sqlite3.Connection] = sqlite3.connect,
) -> DiagnosticResult:
    try:
        conn = connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE kirok_fts_check USING fts5(content)")
        finally:
            conn.close()
    except Exception as exc:
        return DiagnosticResult(
            "sqlite_fts5",
            "fail",
            f"SQLite FTS5 is unavailable: {exc}",
        )
    return DiagnosticResult("sqlite_fts5", "pass", "SQLite FTS5 is available.")


def _check_sqlite_vec(
    connect: Callable[[str], sqlite3.Connection] = sqlite3.connect,
) -> DiagnosticResult:
    """Check that the sqlite-vec extension can load and create a vec0 table.

    This is the actual KNN backend; without it recall silently falls back to the
    slower brute-force cosine path, so a failure here is worth surfacing. The
    load steps mirror ``MemoryDB._load_vec_extension``. The probe dimension is a
    tiny fixed ``float[4]`` — this checks loadability, not the production width.
    """
    try:
        import sqlite_vec  # noqa: F401
    except Exception as exc:
        return DiagnosticResult(
            "sqlite_vec",
            "fail",
            f"sqlite-vec Python package is not importable: {exc}. Run 'uv sync'.",
        )
    try:
        conn = connect(":memory:")
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute(
                "CREATE VIRTUAL TABLE kirok_vec_check USING vec0(embedding float[4])"
            )
        finally:
            conn.close()
    except Exception as exc:
        return DiagnosticResult(
            "sqlite_vec",
            "fail",
            f"sqlite-vec extension failed to load: {exc}",
        )
    return DiagnosticResult("sqlite_vec", "pass", "sqlite-vec (vec0) is available.")


def _check_db_path_writable(raw_path: str | None = None) -> DiagnosticResult:
    path = _resolve_db_path(raw_path)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".kirok-write-", dir=parent, delete=True):
            pass
    except Exception as exc:
        return DiagnosticResult(
            "db_path_writable",
            "fail",
            f"Cannot write to database directory {parent}: {exc}",
        )
    return DiagnosticResult(
        "db_path_writable",
        "pass",
        f"Database directory is writable: {parent}",
    )


def _check_online_embedding() -> DiagnosticResult:
    """Make one live embedding call to confirm the Gemini API is reachable.

    Only runs under `kirok-doctor --online`; the default diagnostics stay fully
    offline. Reports success/failure and the round-trip time.
    """
    import asyncio
    import time

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key.strip():
        return DiagnosticResult(
            "online_embedding",
            "fail",
            "GEMINI_API_KEY is not set; cannot run the online embedding check.",
        )
    try:
        from kirok_mcp.embeddings import EmbeddingClient

        client = EmbeddingClient(api_key=key)
        start = time.perf_counter()
        values = asyncio.run(client.embed("kirok online diagnostic probe"))
        elapsed = time.perf_counter() - start
    except Exception as exc:
        return DiagnosticResult(
            "online_embedding",
            "fail",
            f"Embedding API call failed: {exc}",
        )
    return DiagnosticResult(
        "online_embedding",
        "pass",
        f"Embedding API responded in {elapsed:.2f}s ({len(values)} dims).",
    )


def run_diagnostics(
    env_path: Path | None = None,
    db_path: str | None = None,
    online: bool = False,
) -> list[DiagnosticResult]:
    """Run offline diagnostics without calling Gemini or any network API.

    With ``online=True`` a single live embedding check is appended; the offline
    checks are unchanged either way.
    """
    path = env_path or _env_path()
    load_dotenv(path)

    results = [
        _check_python_version(),
        _check_env_file(path),
        _check_api_key(),
        _check_dependency("mcp"),
        _check_dependency("google.genai"),
        _check_dependency("numpy"),
        _check_dependency("dotenv"),
        _check_sqlite_fts5(),
        _check_sqlite_vec(),
        _check_db_path_writable(db_path or os.environ.get("KIROK_DB_PATH")),
    ]
    if online:
        results.append(_check_online_embedding())
    return results


def _format_text(results: list[DiagnosticResult]) -> str:
    icons = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    lines = ["Kirok diagnostics (offline)", ""]
    for result in results:
        label = icons.get(result.status, result.status.upper())
        lines.append(f"[{label}] {result.name}: {result.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run offline diagnostics for Kirok setup.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostic results as JSON.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override the database path checked for writability.",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Also make one live Gemini embedding call to verify connectivity.",
    )
    args = parser.parse_args(argv)

    results = run_diagnostics(db_path=args.db_path, online=args.online)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(_format_text(results))

    return 1 if any(r.status == "fail" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
