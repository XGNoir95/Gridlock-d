import pytest
from pydantic import ValidationError

from app.schemas import OptimizeRequest
from tests.helpers import valid_request_payload


def test_request_accepts_exact_24_hour_contract() -> None:
    request = OptimizeRequest.model_validate(valid_request_payload())

    assert request.scenario_id == "TEST-001"
    assert [entry.hour for entry in request.hours] == list(range(24))


def test_request_rejects_duplicate_or_missing_hour() -> None:
    payload = valid_request_payload()
    payload["hours"][23]["hour"] = 22

    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(payload)


def test_request_accepts_initial_energy_below_minimum() -> None:
    payload = valid_request_payload()
    payload["battery"]["initial_energy_kwh"] = 10
    payload["battery"]["minimum_energy_kwh"] = 20

    request = OptimizeRequest.model_validate(payload)

    assert request.battery.initial_energy_kwh == 10


def test_request_rejects_unknown_fields_and_non_finite_numbers() -> None:
    payload = valid_request_payload()
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(payload)

    payload = valid_request_payload()
    payload["hours"][0]["demand_kwh"] = float("nan")
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario_id", "x" * 1025),
        ("operator_notes", ["x" * 16385]),
    ],
)
def test_request_rejects_unreasonably_large_strings(field: str, value: object) -> None:
    payload = valid_request_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(payload)
