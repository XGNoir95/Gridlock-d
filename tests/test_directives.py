import pytest

from app.schemas import DirectiveInterpretation, OptimizeRequest
from app.services.directive_engine import DirectiveConflictError, compile_directives
from tests.helpers import valid_request_payload


def directive(kind: str, adjustment: dict | None) -> DirectiveInterpretation:
    return DirectiveInterpretation.model_validate(
        {
            "note_index": 0,
            "applies": kind != "no_op",
            "directive_type": kind,
            "structured_adjustment": adjustment,
            "explanation": "Test directive.",
        }
    )


def test_compiler_applies_each_directive_to_only_listed_hours() -> None:
    payload = valid_request_payload()
    payload["hours"][12]["solar_kwh"] = 80
    request = OptimizeRequest.model_validate(payload)
    directives = [
        directive("solar_reduction", {"hours": [12], "factor": 0.25}),
        DirectiveInterpretation.model_validate(
            {
                "note_index": 1,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": [18], "minimum_energy_kwh": 90},
                "explanation": "Test reserve.",
            }
        ),
        DirectiveInterpretation.model_validate(
            {
                "note_index": 2,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": [19], "max_grid_kwh": 75},
                "explanation": "Test cap.",
            }
        ),
    ]

    compiled = compile_directives(request, directives)

    assert compiled.effective_solar[12] == 20
    assert compiled.effective_solar[11] == 0
    assert compiled.active_minimum[18] == 90
    assert compiled.active_minimum[17] == 20
    assert compiled.grid_upper_bound[19] == 75
    assert compiled.grid_upper_bound[18] is None


def test_compiler_combines_charge_and_discharge_outages() -> None:
    request = OptimizeRequest.model_validate(valid_request_payload())
    directives = [
        directive("no_charge_window", {"hours": [4]}),
        DirectiveInterpretation.model_validate(
            {
                "note_index": 1,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": [4]},
                "explanation": "Test outage.",
            }
        ),
    ]

    compiled = compile_directives(request, directives)

    assert compiled.charge_allowed[4] is False
    assert compiled.discharge_allowed[4] is False


def test_compiler_rejects_conflicting_solar_factors() -> None:
    payload = valid_request_payload()
    payload["hours"][12]["solar_kwh"] = 80
    request = OptimizeRequest.model_validate(payload)
    directives = [
        directive("solar_reduction", {"hours": [12], "factor": 0.5}),
        DirectiveInterpretation.model_validate(
            {
                "note_index": 1,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [12], "factor": 0.25},
                "explanation": "Conflicting reduction.",
            }
        ),
    ]

    with pytest.raises(DirectiveConflictError):
        compile_directives(request, directives)
