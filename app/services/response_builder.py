"""Map solver output to the exact public response schema."""

from math import fsum

from app.schemas import DirectiveInterpretation, HourPlan, OptimizeRequest, OptimizeResponse
from app.services.optimizer import OptimizationSolution

PRECISION = 6
ACTION_EPSILON = 0.000001


def _rounded(value: float) -> float:
    normalized = 0.0 if abs(value) < ACTION_EPSILON else value
    return round(normalized, PRECISION)


def _nonnegative(value: float) -> float:
    if value < -ACTION_EPSILON:
        raise ValueError("solver returned a negative non-negative flow")
    return max(0.0, _rounded(value))


def build_response(
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
    solution: OptimizationSolution,
) -> OptimizeResponse:
    """Build response values once, before independent replay validation."""
    plan: list[HourPlan] = []
    energy = request.battery.initial_energy_kwh
    for hour in range(24):
        flow = _rounded(solution.battery_flow[hour])
        if flow > ACTION_EPSILON:
            action = "charge"
            battery_kwh = flow
            energy += battery_kwh
        elif flow < -ACTION_EPSILON:
            action = "discharge"
            battery_kwh = -flow
            energy -= battery_kwh
        else:
            action = "idle"
            battery_kwh = 0.0
        energy = _rounded(energy)
        plan.append(
            HourPlan(
                hour=hour,
                grid_kwh=_nonnegative(solution.grid[hour]),
                solar_used_kwh=_nonnegative(solution.solar_used[hour]),
                battery_action=action,
                battery_kwh=_rounded(battery_kwh),
                battery_energy_after_kwh=energy,
            )
        )

    total_grid = _rounded(fsum(item.grid_kwh for item in plan))
    total_cost = _rounded(
        fsum(
            plan[hour].grid_kwh * request.hours[hour].tariff_bdt_per_kwh
            for hour in range(24)
        )
    )
    peak_grid = _rounded(max(item.grid_kwh for item in plan))
    applied = [item.directive_type for item in directives if item.applies]
    summary = (
        "Applied " + ", ".join(applied) + " and minimized grid electricity cost."
        if applied
        else "No operator note changed the schedule; minimized grid electricity cost."
    )
    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=summary,
    )
