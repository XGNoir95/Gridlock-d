import json
from collections.abc import Callable

import httpx
import pytest

from app.schemas import OptimizeRequest
from app.services.credential_pool import CredentialPool
from app.services.interpreter import GeminiProvider, GroqProvider, ProviderError
from tests.helpers import valid_request_payload
from tests.test_api import no_op_envelope


def request() -> OptimizeRequest:
    return OptimizeRequest.model_validate(valid_request_payload())


def gemini_success() -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": no_op_envelope().model_dump_json()}],
                }
            }
        ]
    }


def groq_success() -> dict:
    return {
        "choices": [
            {"message": {"content": no_op_envelope().model_dump_json()}}
        ]
    }


@pytest.mark.asyncio
async def test_gemini_auth_failure_marks_key_for_future_request_without_retrying() -> None:
    observed_keys: list[str] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        observed_keys.append(incoming.headers["x-goog-api-key"])
        if len(observed_keys) == 1:
            return httpx.Response(401, json={"error": "credential rejected"})
        return httpx.Response(200, json=gemini_success())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(
        pool=CredentialPool(["gemini-secret-1", "gemini-secret-2"]),
        model="configured-model",
        timeout_seconds=1,
        client=client,
    )

    with pytest.raises(ProviderError) as caught:
        await provider.generate(request())
    result = await provider.generate(request())
    await client.aclose()

    assert result == no_op_envelope()
    assert observed_keys == ["gemini-secret-1", "gemini-secret-2"]
    assert "gemini-secret-1" not in str(caught.value)
    assert caught.value.category == "http_401"


@pytest.mark.asyncio
async def test_groq_auth_failure_marks_key_for_future_event_without_retrying() -> None:
    observed_keys: list[str] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        observed_keys.append(incoming.headers["authorization"])
        if len(observed_keys) == 1:
            return httpx.Response(403, json={"error": "credential rejected"})
        return httpx.Response(200, json=groq_success())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GroqProvider(
        pool=CredentialPool(["groq-secret-1", "groq-secret-2"]),
        model="configured-model",
        timeout_seconds=1,
        client=client,
    )

    with pytest.raises(ProviderError) as caught:
        await provider.generate(request())
    result = await provider.generate(request())
    await client.aclose()

    assert result == no_op_envelope()
    assert observed_keys == ["Bearer groq-secret-1", "Bearer groq-secret-2"]
    assert "groq-secret-1" not in str(caught.value)
    assert caught.value.category == "http_403"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_factory", "response_factory"),
    [
        (
            lambda client: GeminiProvider(
                pool=CredentialPool(["secret"]),
                model="model",
                timeout_seconds=1,
                client=client,
            ),
            gemini_success,
        ),
        (
            lambda client: GroqProvider(
                pool=CredentialPool(["secret"]),
                model="model",
                timeout_seconds=1,
                client=client,
            ),
            groq_success,
        ),
    ],
)
async def test_providers_return_the_same_internal_envelope(
    provider_factory: Callable[[httpx.AsyncClient], object],
    response_factory: Callable[[], dict],
) -> None:
    captured_body: dict = {}

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(incoming.content))
        return httpx.Response(200, json=response_factory())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = provider_factory(client)
    result = await provider.generate(request())  # type: ignore[attr-defined]
    await client.aclose()

    assert result == no_op_envelope()
    assert captured_body


@pytest.mark.asyncio
async def test_groq_uses_unambiguous_wire_schema_and_returns_internal_envelope() -> None:
    wire_result = {
        "directive_interpretation": [
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {
                    "hours": [13, 14],
                    "factor": 0.2,
                    "minimum_energy_kwh": None,
                    "max_grid_kwh": None,
                },
                "explanation": "Solar availability is reduced.",
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

    def handler(incoming: httpx.Request) -> httpx.Response:
        body = json.loads(incoming.content)
        schema = body["response_format"]["json_schema"]["schema"]
        adjustment = schema["$defs"]["DirectiveInterpretation"]["properties"][
            "structured_adjustment"
        ]
        assert [variant.get("type") for variant in adjustment["anyOf"]] == [
            "object",
            "null",
        ]
        object_variant = adjustment["anyOf"][0]
        assert set(object_variant["required"]) == {
            "hours",
            "factor",
            "minimum_energy_kwh",
            "max_grid_kwh",
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(wire_result)}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GroqProvider(
        pool=CredentialPool(["secret"]),
        model="configured-model",
        timeout_seconds=1,
        client=client,
    )

    result = await provider.generate(request())
    await client.aclose()

    adjustment = result.directive_interpretation[0].structured_adjustment
    assert adjustment is not None
    assert adjustment.model_dump() == {"hours": [13, 14], "factor": 0.2}
