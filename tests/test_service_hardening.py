import asyncio
from time import perf_counter

import httpx
import pytest

from app.main import create_app
from app.schemas import InterpretationEnvelope, OptimizeRequest
from app.services.interpreter import InterpretationProvider
from app.services.planner import Planner
from tests.helpers import valid_request_payload
from tests.test_api import no_op_envelope


class BlockingProvider(InterpretationProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(
        self,
        request: OptimizeRequest,
        correction: str | None = None,
    ) -> InterpretationEnvelope:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return no_op_envelope()


class SlowProvider(InterpretationProvider):
    async def generate(
        self,
        request: OptimizeRequest,
        correction: str | None = None,
    ) -> InterpretationEnvelope:
        await asyncio.sleep(1)
        return no_op_envelope()


class ClosableProvider(InterpretationProvider):
    def __init__(self) -> None:
        self.close_calls = 0

    async def generate(
        self,
        request: OptimizeRequest,
        correction: str | None = None,
    ) -> InterpretationEnvelope:
        return no_op_envelope()

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_before_model_call() -> None:
    provider = BlockingProvider()
    app = create_app(
        Planner(primary=provider),
        max_request_body_bytes=1024,
    )
    payload = valid_request_payload()
    payload["operator_notes"] = ["x" * 2000]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/optimize-energy", json=payload)

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid request"}
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_capacity_gate_rejects_excess_work_without_blocking_health() -> None:
    provider = BlockingProvider()
    app = create_app(
        Planner(primary=provider),
        max_concurrent_optimizations=1,
        max_queued_optimizations=0,
        total_request_deadline_seconds=1,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.post("/optimize-energy", json=valid_request_payload()))
        await asyncio.wait_for(provider.entered.wait(), timeout=0.5)

        excess = await client.post("/optimize-energy", json=valid_request_payload())
        health = await client.get("/health")
        provider.release.set()
        completed = await asyncio.wait_for(first, timeout=1)

    assert excess.status_code == 500
    assert excess.json() == {"detail": "Optimization failed"}
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert completed.status_code == 200
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_complete_request_has_an_end_to_end_deadline() -> None:
    app = create_app(
        Planner(primary=SlowProvider()),
        total_request_deadline_seconds=0.05,
    )
    transport = httpx.ASGITransport(app=app)

    started = perf_counter()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/optimize-energy", json=valid_request_payload())
    elapsed = perf_counter() - started

    assert response.status_code == 500
    assert response.json() == {"detail": "Optimization failed"}
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_planner_closes_each_owned_provider_once() -> None:
    primary = ClosableProvider()
    backup = ClosableProvider()
    planner = Planner(primary=primary, backup=backup)

    await planner.aclose()

    assert primary.close_calls == 1
    assert backup.close_calls == 1


@pytest.mark.asyncio
async def test_security_headers_are_added_to_json_responses() -> None:
    app = create_app(Planner(primary=ClosableProvider()))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
