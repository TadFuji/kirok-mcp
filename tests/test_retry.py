import asyncio
import unittest
from unittest.mock import patch

import httpx
from google.genai.errors import ClientError, ServerError

from kirok_mcp.retry import _is_transient, with_retry


def _client_error(code: int) -> ClientError:
    return ClientError(code, {"error": {"message": "boom", "status": "ERR"}})


def _server_error(code: int = 503) -> ServerError:
    return ServerError(code, {"error": {"message": "boom", "status": "ERR"}})


async def _no_sleep(*_args, **_kwargs) -> None:
    return None


class IsTransientTest(unittest.TestCase):
    def test_server_error_is_transient(self) -> None:
        self.assertTrue(_is_transient(_server_error()))

    def test_rate_limit_is_transient(self) -> None:
        self.assertTrue(_is_transient(_client_error(429)))

    def test_other_client_error_is_not_transient(self) -> None:
        self.assertFalse(_is_transient(_client_error(403)))
        self.assertFalse(_is_transient(_client_error(400)))

    def test_unknown_exception_is_not_transient(self) -> None:
        self.assertFalse(_is_transient(ValueError("nope")))

    def test_httpx_transport_error_is_transient(self) -> None:
        self.assertTrue(_is_transient(httpx.ConnectError("conn refused")))

    def test_httpx_timeout_error_is_transient(self) -> None:
        self.assertTrue(_is_transient(httpx.ReadTimeout("timed out")))


class WithRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_then_succeeds(self) -> None:
        attempts = {"n": 0}

        async def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise _server_error()
            return "ok"

        with patch("asyncio.sleep", new=_no_sleep):
            result = await with_retry(lambda: flaky(), attempts=3, base_delay=0.0)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts["n"], 3)

    async def test_raises_after_exhausting_attempts(self) -> None:
        attempts = {"n": 0}

        async def always_fail():
            attempts["n"] += 1
            raise _server_error()

        with patch("asyncio.sleep", new=_no_sleep):
            with self.assertRaises(ServerError):
                await with_retry(lambda: always_fail(), attempts=3, base_delay=0.0)

        self.assertEqual(attempts["n"], 3)

    async def test_non_transient_raises_immediately(self) -> None:
        attempts = {"n": 0}

        async def auth_fail():
            attempts["n"] += 1
            raise _client_error(403)

        with patch("asyncio.sleep", new=_no_sleep):
            with self.assertRaises(ClientError):
                await with_retry(lambda: auth_fail(), attempts=3, base_delay=0.0)

        self.assertEqual(attempts["n"], 1)

    async def test_backoff_delays_grow_per_attempt(self) -> None:
        delays = []

        async def record_sleep(d):
            delays.append(d)

        async def always_fail():
            raise _server_error()

        with patch("asyncio.sleep", new=record_sleep):
            with self.assertRaises(ServerError):
                await with_retry(
                    lambda: always_fail(), attempts=3, base_delay=1.0, max_delay=8.0
                )

        # 3 attempts → 2 sleeps. delay_i = base*2**i + jitter[0, base):
        # [1,2) then [2,3). Verifies the exponential formula, not just the count.
        self.assertEqual(len(delays), 2)
        self.assertGreaterEqual(delays[0], 1.0)
        self.assertLess(delays[0], 2.0)
        self.assertGreaterEqual(delays[1], 2.0)
        self.assertLess(delays[1], 3.0)

    async def test_cancelled_error_is_reraised_immediately(self) -> None:
        attempts = {"n": 0}

        async def cancelled():
            attempts["n"] += 1
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", new=_no_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await with_retry(lambda: cancelled(), attempts=3, base_delay=0.0)

        self.assertEqual(attempts["n"], 1)


if __name__ == "__main__":
    unittest.main()
