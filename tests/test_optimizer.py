import pytest

from app.schemas import OptimizeRequest
from app.services.directive_engine import compile_directives
from app.services.optimizer import OptimizationError, solve_schedule
from tests.helpers import valid_request_payload


def test_optimizer_charges_before_expensive_hour_and_returns_to_initial_state() -> None:
    payload = valid_request_payload()
    payload["battery"] = {
        "capacity_kwh": 50,
        "initial_energy_kwh": 0,
        "minimum_energy_kwh": 0,
        "max_charge_kwh_per_hour": 50,
        "max_discharge_kwh_per_hour": 50,
    }
    for entry in payload["hours"]:
        entry["tariff_bdt_per_kwh"] = 1
    payload["hours"][1]["tariff_bdt_per_kwh"] = 10
    request = OptimizeRequest.model_validate(payload)
    compiled = compile_directives(request, [])

    solution = solve_schedule(request, compiled)

    assert solution.battery_flow[0] == pytest.approx(50)
    assert solution.battery_flow[1] == pytest.approx(-50)
    assert solution.battery_energy_after[23] == pytest.approx(0)
    assert sum(
        solution.grid[hour] * request.hours[hour].tariff_bdt_per_kwh
        for hour in range(24)
    ) == pytest.approx(2850)


def test_optimizer_reports_below_minimum_initial_energy_as_infeasible() -> None:
    payload = valid_request_payload()
    payload["battery"]["initial_energy_kwh"] = 10
    payload["battery"]["minimum_energy_kwh"] = 20
    request = OptimizeRequest.model_validate(payload)

    with pytest.raises(OptimizationError, match="infeasible"):
        solve_schedule(request, compile_directives(request, []))
