"""Async HTTP client wrapper.

Adds:
- per-host token-bucket rate limiting
- transient-error classification (network, 5xx, 429)
- bounded exponential backoff retry via ``tenacity``
- structured logging of every request

Deliberately NO authentication. Phase 0-9 only hit public endpoints.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..settings import HttpSettings
from .rate_limit import RateLimiter


class HttpError(RuntimeError):
    """Non-transient HTTP failure: caller should not retry."""

    def __init__(self, *args: object, response: httpx.Response | None = None) -> None:
        super().__init__(*args)
        self.response = response


class TransientError(RuntimeError):
    """Transient HTTP/network failure: retried internally; raised only after
    the retry budget is exhausted."""


_TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def _classify(exc: BaseException) -> type[BaseException]:
    """Map the underlying exception to one of (TransientError, HttpError)."""

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in _TRANSIENT_HTTP_STATUSES:
            return TransientError
        return HttpError
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError):
        return TransientError
    return HttpError


class AsyncHttpClient:
    """Thin async wrapper around `httpx.AsyncClient`.

    Use as an async context manager, or call ``aclose()`` manually."""

    def __init__(self, settings: HttpSettings) -> None:
        self._settings = settings
        self._rate = RateLimiter(settings.rate_limits)
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_s,
                read=settings.read_timeout_s,
                write=settings.read_timeout_s,
                pool=settings.read_timeout_s,
            ),
            follow_redirects=True,
            headers={"User-Agent": "polymarket-arb/0.0.1 (+research)"},
        )

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _do(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        await self._rate.acquire(url)
        started = time.monotonic()
        resp = await self._client.request(method, url, **kwargs)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.bind(http_method=method, http_url=url, http_status=resp.status_code,
                    http_elapsed_ms=elapsed_ms).debug("http request")
        resp.raise_for_status()
        return resp

    async def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        """Issue a request with retries; return parsed JSON."""

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._settings.max_retries),
                wait=wait_exponential(
                    multiplier=self._settings.backoff_initial_s,
                    max=self._settings.backoff_max_s,
                ),
                retry=retry_if_exception_type(TransientError),
                reraise=True,
            ):
                with attempt:
                    try:
                        resp = await self._do(method, url, **kwargs)
                    except BaseException as exc:
                        kind = _classify(exc)
                        if kind is TransientError:
                            raise TransientError(str(exc)) from exc
                        raise HttpError(str(exc), response=getattr(exc, "response", None)) from exc
                    return resp.json()
        except RetryError as exc:  # pragma: no cover — `reraise=True` should make this unreachable
            raise TransientError("retry budget exhausted") from exc

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        return await self.request_json("GET", url, **kwargs)

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        return await self.request_json("POST", url, **kwargs)
