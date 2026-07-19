"""Retry wiring for the LLMClient methods.

WHY: evaluate_importance / deduplicate swallow errors and fall back to a safe
default (fail-open), and consolidate raises (its caller must NOT mark memories
consolidated on failure — see test_hybrid_improvements). Before v1.2.1 these
called the API without retry, so a single transient 5xx/429 silently produced
the fallback — e.g. a duplicate memory stored because deduplication "failed".
These tests pin that one transient error is retried (the real result comes
back), while non-transient errors still fail fast without retries.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from google.genai.errors import ClientError, ServerError

from kirok_mcp.llm import LLMClient


def _server_error() -> ServerError:
    return ServerError(503, {"error": {"message": "boom", "status": "ERR"}})


def _auth_error() -> ClientError:
    return ClientError(403, {"error": {"message": "denied", "status": "ERR"}})


async def _no_sleep(*_args, **_kwargs) -> None:
    return None


class _FlakyModels:
    """generate_content that raises `errors` first, then returns `text`."""

    def __init__(self, errors: list[Exception], text: str):
        self.errors = list(errors)
        self.text = text
        self.calls = 0

    async def generate_content(self, **_kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return SimpleNamespace(text=self.text)


def _make_client(models: _FlakyModels) -> LLMClient:
    llm = LLMClient(api_key="test-key")
    llm.client = SimpleNamespace(aio=SimpleNamespace(models=models))
    return llm


class LLMRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicate_retries_transient_instead_of_failing_open(self) -> None:
        models = _FlakyModels(
            [_server_error()], '{"action": "noop", "reason": "duplicate"}'
        )
        llm = _make_client(models)

        with patch("asyncio.sleep", new=_no_sleep):
            result = await llm.deduplicate(
                "new content", [{"id": "m1", "similarity": 0.9, "content": "old"}]
            )

        # Without retry this would fail open to {"action": "add", ...}.
        self.assertEqual(result["action"], "noop")
        self.assertEqual(models.calls, 2)

    async def test_evaluate_importance_retries_transient(self) -> None:
        models = _FlakyModels([_server_error()], '{"score": 8, "reason": "durable"}')
        llm = _make_client(models)

        with patch("asyncio.sleep", new=_no_sleep):
            result = await llm.evaluate_importance("content", threshold=5)

        # Without retry this would fail open to score == threshold.
        self.assertEqual(result["score"], 8)
        self.assertEqual(models.calls, 2)

    async def test_consolidate_retries_transient(self) -> None:
        models = _FlakyModels(
            [_server_error()],
            '[{"action": "create", "content": "obs", "source_memory_ids": ["m1"]}]',
        )
        llm = _make_client(models)

        with patch("asyncio.sleep", new=_no_sleep):
            result = await llm.consolidate(
                new_memories=[{"id": "m1", "content": "x"}],
                existing_observations=[],
            )

        # Without retry this would fail open to [].
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["action"], "create")
        self.assertEqual(models.calls, 2)

    async def test_deduplicate_non_transient_fails_open_without_retry(self) -> None:
        models = _FlakyModels([_auth_error(), _auth_error(), _auth_error()], "{}")
        llm = _make_client(models)

        with patch("asyncio.sleep", new=_no_sleep):
            result = await llm.deduplicate(
                "new content", [{"id": "m1", "similarity": 0.9, "content": "old"}]
            )

        # Auth errors are not retried (would fail identically) and the
        # fail-open default still protects the retain.
        self.assertEqual(result["action"], "add")
        self.assertEqual(models.calls, 1)


if __name__ == "__main__":
    unittest.main()
