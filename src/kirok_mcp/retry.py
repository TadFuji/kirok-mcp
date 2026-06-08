"""Lightweight async retry with exponential backoff for transient Gemini errors.

A single transient network blip (a 5xx, a 429 rate limit, a dropped connection)
otherwise fails a whole recall/retain. This wraps the API calls in a small,
bounded retry so those one-off failures recover silently, while structured
client errors (bad request / auth) still fail fast.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import httpx
from google.genai.errors import ClientError, ServerError

T = TypeVar("T")

_DEFAULT_LOGGER = logging.getLogger("kirok.retry")


def _is_transient(exc: BaseException) -> bool:
    """Return True for errors worth retrying.

    Transient: server-side 5xx (``ServerError``), rate limiting (``ClientError``
    with HTTP ``code == 429``), and network transport/timeout errors. Everything
    else — other 4xx client errors (400/401/403 = bad request / auth) and any
    unknown exception — is treated as non-transient and re-raised immediately, so
    we never burn quota retrying a request that will fail identically.
    """
    if isinstance(exc, ServerError):
        return True
    if isinstance(exc, ClientError):
        return getattr(exc, "code", None) == 429
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    return False


async def with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
    logger: logging.Logger | None = None,
) -> T:
    """Run ``coro_factory()`` with retry on transient failures.

    ``coro_factory`` MUST return a fresh awaitable on each call (e.g. a lambda
    wrapping the API call) — an already-awaited coroutine cannot be re-awaited.

    The total wait stays small (a few seconds across ``attempts``) so it fits
    inside the server's outer ``asyncio.wait_for`` budgets. ``CancelledError`` is
    re-raised immediately so those timeouts (and shutdown) still fire promptly.
    """
    log = logger or _DEFAULT_LOGGER
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not _is_transient(exc) or attempt == attempts - 1:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(
                0, base_delay
            )
            log.warning(
                "Transient API error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                attempts,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    # Unreachable: the loop returns on success or raises on the final attempt.
    raise RuntimeError("with_retry exhausted without returning or raising")
