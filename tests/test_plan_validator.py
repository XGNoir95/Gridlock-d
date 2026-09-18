import pytest

from app.schemas import DirectiveInterpretation, OptimizeRequest
from app.services.directive_engine import compile_directives
from app.services.optimizer import solve_schedule
from app.services.plan_validator import PlanValidationError, validate_plan
from app.services.response_builder import build_response
from tests.helpers import valid_request_payload


def no_op() -> DirectiveInterpretation:
    return DirectiveInterpretation.model_validate(
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "No scheduling effect.",
        }
    )


def valid_response_fixture():
    request = OptimizeRequest.model_validate(valid_request_payload())
    directives = [no_op()]
    compiled = compile_directives(request, directives)
    solution = solve_schedule(request, compiled)
    response = build_response(request, directives, solution)
    return request, directives, compiled, response


def test_replay_accepts_valid_serialized_plan_and_totals() -> None:
    request, directives, compiled, response = valid_response_fixture()

    validate_plan(request, directives, compiled, response)

    assert response.total_grid_kwh == pytest.approx(
        sum(entry.grid_kwh for entry in response.hourly_plan), abs=0.01
    )


def test_replay_rejects_hourly_energy_balance_corruption() -> None:
    request, directives, compiled, response = valid_response_fixture()
    corrupted_hour = response.hourly_plan[0].model_copy(
        update={"grid_kwh": response.hourly_plan[0].grid_kwh + 1}
    )
    corrupted = response.model_copy(
        update={"hourly_plan": [corrupted_hour, *response.hourly_plan[1:]]}
    )

    with pytest.raises(PlanValidationError, match="energy balance"):
        validate_plan(request, directives, compiled, corrupted)


def test_replay_rejects_reported_battery_state_corruption() -> None:
    request, directives, compiled, response = valid_response_fixture()
    corrupted_hour = response.hourly_plan[0].model_copy(
        update={
            "battery_energy_after_kwh": response.hourly_plan[0].battery_energy_after_kwh + 1
        }
    )
    corrupted = response.model_copy(
        update={"hourly_plan": [corrupted_hour, *response.hourly_plan[1:]]}
    )

    with pytest.raises(PlanValidationError, match="battery transition"):
        validate_plan(request, directives, compiled, corrupted)
