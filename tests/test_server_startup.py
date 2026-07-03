"""Import-time and startup behavior of kirok_mcp.server.

Importing the module must be side-effect-free (no database creation, no
process exit without an API key); the API-key requirement is enforced at
startup (main) instead. Each case runs in a fresh interpreter because the
behavior under test is what happens at import / startup time.

GEMINI_API_KEY is set to "" (not removed) in the child environment: dotenv
does not override existing variables, so this masks any key in the repo's
.env and works the same on developer machines and CI.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _run_python(code: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **extra_env}
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


class ServerStartupTest(unittest.TestCase):
    def test_import_without_api_key_neither_exits_nor_touches_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            proc = _run_python(
                "import kirok_mcp.server",
                {"GEMINI_API_KEY": "", "KIROK_DB_PATH": str(db_path)},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(
                db_path.exists(),
                "importing kirok_mcp.server must not create the database",
            )

    def test_main_without_api_key_exits_with_error(self) -> None:
        proc = _run_python(
            "from kirok_mcp.server import main; main()",
            {"GEMINI_API_KEY": ""},
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("GEMINI_API_KEY", proc.stderr)


if __name__ == "__main__":
    unittest.main()
