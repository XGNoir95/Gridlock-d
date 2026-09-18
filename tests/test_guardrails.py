import pytest

from app.schemas import InterpretationEnvelope, OptimizeRequest
from app.services.guardrails import GuardrailError, validate_interpretation
from tests.helpers import valid_request_payload


def request() -> OptimizeRequest:
    return OptimizeRequest.model_validate(valid_request_payload())


def test_guardrail_accepts_reduction_fraction_and_end_exclusive_hours() -> None:
    envelope = InterpretationEnvelope.model_validate(
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
                    "explanation": "An 80% reduction leaves 20% usable solar.",
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

    directives = validate_interpretation(request(), envelope)

    assert directives[0].structured_adjustment.factor == pytest.approx(0.2)


def test_guardrail_rejects_candidate_that_disagrees_with_numeric_trace() -> None:
    envelope = InterpretationEnvelope.model_validate(
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "minimum_battery_reserve",
                    "structured_adjustment": {
                        "hours": [18, 19, 20],
                        "minimum_energy_kwh": 90,
                    },
                    "explanation": "Half of capacity is required.",
                }
            ],
            "normalization_trace": [
                {
                    "note_index": 0,
                    "time_window": {"start_hour": 18, "end_hour_exclusive": 21},
                    "explicit_hours": None,
                    "numeric_basis": {"kind": "capacity_fraction", "value": 0.5},
                }
            ],
        }
    )

    with pytest.raises(GuardrailError, match="numeric basis"):
        validate_interpretation(request(), envelope)


def test_guardrail_expands_cross_midnight_window_in_sorted_order() -> None:
    envelope = InterpretationEnvelope.model_validate(
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "no_charge_window",
                    "structured_adjustment": {"hours": [0, 23]},
                    "explanation": "Charging is unavailable across midnight.",
                }
            ],
            "normalization_trace": [
                {
                    "note_index": 0,
                    "time_window": {"start_hour": 23, "end_hour_exclusive": 1},
                    "explicit_hours": None,
                    "numeric_basis": {"kind": "none", "value": None},
                }
            ],
        }
    )

    directives = validate_interpretation(request(), envelope)

    assert directives[0].structured_adjustment.hours == [0, 23]


def test_guardrail_accepts_exact_no_op_semantics() -> None:
    envelope = InterpretationEnvelope.model_validate(
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": False,
                    "directive_type": "no_op",
                    "structured_adjustment": None,
                    "explanation": "The note is unrelated to today's energy schedule.",
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

    directives = validate_interpretation(request(), envelope)

    assert directives[0].directive_type == "no_op"


def test_guardrail_accepts_sorted_explicit_disjoint_hours() -> None:
    envelope = InterpretationEnvelope.model_validate(
        {
            "directive_interpretation": [
                {
                    "note_index": 0,
                    "applies": True,
                    "directive_type": "no_discharge_window",
                    "structured_adjustment": {"hours": [1, 3, 5]},
                    "explanation": "Discharge is unavailable in the listed hours.",
                }
            ],
            "normalization_trace": [
                {
                    "note_index": 0,
                    "time_window": None,
                    "explicit_hours": [1, 3, 5],
                    "numeric_basis": {"kind": "none", "value": None},
                }
            ],
        }
    )

    directives = validate_interpretation(request(), envelope)

    assert directives[0].structured_adjustment.hours == [1, 3, 5]
