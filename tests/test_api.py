from typing import Any

import httpx
import pytest

from app.main import create_app
from app.schemas import InterpretationEnvelope, OptimizeRequest
from app.services.interpreter import InterpretationProvider, ProviderError
from app.services.planner import Planner
from tests.helpers import valid_request_payload


class FakeProvider(InterpretationProvider):
    def __init__(self, envelopes: list[InterpretationEnvelope]) -> None:
        self.envelopes = envelopes
        self.calls = 0

    async def generate(
        self,
        request: OptimizeRequest,
        correction: str | None = None,
    ) -> InterpretationEnvelope:
        envelope = self.envelopes[min(self.calls, len(self.envelopes) - 1)]
        self.calls += 1
        return envelope


class FailingProvider(InterpretationProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        request: OptimizeRequest,
        correction: str | None = None,
    ) -> InterpretationEnvelope:
        self.calls += 1
        raise ProviderError("simulated outage")


def no_op_envelope() -> InterpretationEnvelope:
    return InterpretationEnvelope.model_validate(
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": False,
                    "directive_type": "no_op",
                    "structured_adjustment": None,
                    "explanation": "The note does not affect today's energy schedule.",
                }
            ],
            "normalization_trace": [
                {
                    "note_index": 0,
                    "time_window": None,
                    "explicit_hours": None,
                    "numeric_basis": None,
                }
            ],
        }
    )


def mismatched_envelope() -> InterpretationEnvelope:
    return InterpretationEnvelope.model_validate(
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": [13, 14], "factor": 0.8},
                    "explanation": "Incorrect inversion.",
                }
            ],
            "normalization_trace": [
                {
                    "note_index": 0,
                    "time_window": {"start_hour": 13, "end_hour_exclusive": 15},
                    "explicit_hours": None,
                    "numeric_basis": {"kind": "reduction_fraction", "value": 0.8},
                }
            ],
        }
    )


async def post(app: Any, payload: dict[str, Any]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/optimize-energy", json=payload)


@pytest.mark.asyncio
async def test_optimize_energy_runs_full_pipeline() -> None:
    provider = FakeProvider([no_op_envelope()])
    app = create_app(Planner(primary=provider))

    response = await post(app, valid_request_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["scenario_id"] == "TEST-001"
    assert len(body["directive_interpretation"]) == 1
    assert len(body["hourly_plan"]) == 24
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_structurally_invalid_request_returns_400_before_model_call() -> None:
    provider = FakeProvider([no_op_envelope()])
    app = create_app(Planner(primary=provider))
    payload = valid_request_payload()
    payload["hours"] = payload["hours"][:-1]

    response = await post(app, payload)

    assert response.status_code == 400
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_invalid_model_output_repairs_once_then_fails_closed() -> None:
    provider = FakeProvider([mismatched_envelope(), mismatched_envelope()])
    app = create_app(Planner(primary=provider))

    response = await post(app, valid_request_payload())

    assert response.status_code == 500
    assert response.json() == {"detail": "Optimization failed"}
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_backup_attempt_does_not_create_a_third_repair_call() -> None:
    primary = FailingProvider()
    backup = FakeProvider([mismatched_envelope()])
    app = create_app(Planner(primary=primary, backup=backup))

    response = await post(app, valid_request_payload())

    assert response.status_code == 500
    assert primary.calls == 1
    assert backup.calls == 1
