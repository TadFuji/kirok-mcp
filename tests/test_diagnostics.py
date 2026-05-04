import json
import sqlite3
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from kirok_mcp import diagnostics


class DiagnosticsTest(unittest.TestCase):
    def test_api_key_check_hides_value(self) -> None:
        result = diagnostics._check_api_key({"GEMINI_API_KEY": "secret-key"})

        self.assertEqual(result.status, "pass")
        self.assertNotIn("secret-key", result.message)
        self.assertIn("hidden", result.message)

    def test_api_key_check_fails_when_missing(self) -> None:
        result = diagnostics._check_api_key({})

        self.assertEqual(result.status, "fail")
        self.assertIn("not set", result.message)

    def test_sqlite_fts5_failure_is_reported(self) -> None:
        def failing_connect(path: str):
            raise sqlite3.OperationalError("no such module: fts5")

        result = diagnostics._check_sqlite_fts5(connect=failing_connect)

        self.assertEqual(result.status, "fail")
        self.assertIn("FTS5", result.message)

    def test_db_path_writable_uses_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nested" / "memory.db"

            result = diagnostics._check_db_path_writable(str(db_path))

            self.assertEqual(result.status, "pass")
            self.assertTrue(db_path.parent.exists())

    def test_json_output_does_not_print_api_key(self) -> None:
        fake_results = [
            diagnostics.DiagnosticResult(
                "gemini_api_key",
                "pass",
                "GEMINI_API_KEY is set. Value hidden.",
            )
        ]

        with patch.object(diagnostics, "run_diagnostics", return_value=fake_results):
            with patch("sys.stdout", new_callable=StringIO) as stdout:
                exit_code = diagnostics.main(["--json"])

        self.assertEqual(exit_code, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed[0]["name"], "gemini_api_key")
        self.assertNotIn("secret", stdout.getvalue())

    def test_main_returns_failure_when_any_check_fails(self) -> None:
        fake_results = [
            diagnostics.DiagnosticResult("python_version", "pass", "ok"),
            diagnostics.DiagnosticResult("gemini_api_key", "fail", "missing"),
        ]

        with patch.object(diagnostics, "run_diagnostics", return_value=fake_results):
            with patch("sys.stdout", new_callable=StringIO):
                exit_code = diagnostics.main([])

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
