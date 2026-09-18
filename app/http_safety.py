"""Small ASGI safeguards for the public competition API."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class OptimizationCapacityError(RuntimeError):
    """Raised when both active and queued optimization capacity are full."""


class OptimizationGate:
    """Bound active work and queued callers without blocking the health endpoint."""

    def __init__(self, *, max_active: int, max_queued: int) -> None:
        if max_active < 1 or max_queued < 0:
            raise ValueError("optimization capacity limits are invalid")
        self._max_active = max_active
        self._max_queued = max_queued
        self._active = 0
        self._waiting = 0
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._active >= self._max_active:
                if self._waiting >= self._max_queued:
                    raise OptimizationCapacityError("optimization capacity is full")
                self._waiting += 1
                try:
                    await self._condition.wait_for(
                        lambda: self._active < self._max_active
                    )
                finally:
                    self._waiting -= 1
            self._active += 1

        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify(1)


class RequestBodyLimitMiddleware:
    """Reject an oversized optimization body before JSON parsing or model use."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/optimize-energy":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self._max_body_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay_receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(status_code=400, content={"detail": "Invalid request"})
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Add browser-safe, cache-safe headers to every API response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"cache-control", b"no-store"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_headers)
