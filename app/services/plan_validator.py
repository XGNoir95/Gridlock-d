"""Independent replay validator for serialized optimization responses."""

from math import fsum, isclose, isfinite

from app.schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse
from app.services.directive_engine import CompiledDirectives

TOLERANCE = 0.01


class PlanValidationError(RuntimeError):
    """Raised when a response fails independent deterministic replay."""


def _close(actual: float, expected: float) -> bool:
    return isclose(actual, expected, rel_tol=0, abs_tol=TOLERANCE)


def validate_plan(
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
    compiled: CompiledDirectives,
    response: OptimizeResponse,
) -> None:
    """Replay the exact serialized plan and reject any invalid response."""
    if response.scenario_id != request.scenario_id:
        raise PlanValidationError("scenario_id mismatch")
    if response.directive_interpretation != directives:
        raise PlanValidationError("directive interpretation mismatch")
    if [item.note_index for item in directives] != list(range(len(request.operator_notes))):
        raise PlanValidationError("directive note mapping mismatch")
    if [item.hour for item in response.hourly_plan] != list(range(24)):
        raise PlanValidationError("hourly_plan must contain ordered hours 0 through 23")

    energy = request.battery.initial_energy_kwh
    for hour, item in enumerate(response.hourly_plan):
        numeric_values = (
            item.grid_kwh,
            item.solar_used_kwh,
            item.battery_kwh,
            item.battery_energy_after_kwh,
        )
        if not all(isfinite(value) and value >= 0 for value in numeric_values):
            raise PlanValidationError(f"invalid numeric value at hour {hour}")
        charge = item.battery_kwh if item.battery_action == "charge" else 0.0
        discharge = item.battery_kwh if item.battery_action == "discharge" else 0.0
        if item.battery_action == "idle" and item.battery_kwh != 0:
            raise PlanValidationError(f"idle battery magnitude at hour {hour}")
        if item.battery_action != "idle" and item.battery_kwh <= TOLERANCE:
            raise PlanValidationError(f"non-idle action has zero magnitude at hour {hour}")
        if charge > request.battery.max_charge_kwh_per_hour + TOLERANCE:
            raise PlanValidationError(f"charge rate exceeded at hour {hour}")
        if discharge > request.battery.max_discharge_kwh_per_hour + TOLERANCE:
            raise PlanValidationError(f"discharge rate exceeded at hour {hour}")
        if charge > TOLERANCE and not compiled.charge_allowed[hour]:
            raise PlanValidationError(f"charge forbidden at hour {hour}")
        if discharge > TOLERANCE and not compiled.discharge_allowed[hour]:
            raise PlanValidationError(f"discharge forbidden at hour {hour}")

        energy = energy + charge - discharge
        if not _close(item.battery_energy_after_kwh, energy):
            raise PlanValidationError(f"battery transition mismatch at hour {hour}")
        if energy < compiled.active_minimum[hour] - TOLERANCE:
            raise PlanValidationError(f"battery minimum violated at hour {hour}")
        if energy > request.battery.capacity_kwh + TOLERANCE:
            raise PlanValidationError(f"battery capacity violated at hour {hour}")
        if item.solar_used_kwh > compiled.effective_solar[hour] + TOLERANCE:
            raise PlanValidationError(f"effective solar exceeded at hour {hour}")
        grid_cap = compiled.grid_upper_bound[hour]
        if grid_cap is not None and item.grid_kwh > grid_cap + TOLERANCE:
            raise PlanValidationError(f"grid cap exceeded at hour {hour}")

        supply = item.grid_kwh + item.solar_used_kwh + discharge
        use = request.hours[hour].demand_kwh + charge
        if not _close(supply, use):
            raise PlanValidationError(f"energy balance mismatch at hour {hour}")

    if not _close(energy, request.battery.initial_energy_kwh):
        raise PlanValidationError("end-of-day battery neutrality violated")

    total_grid = fsum(item.grid_kwh for item in response.hourly_plan)
    total_cost = fsum(
        response.hourly_plan[hour].grid_kwh * request.hours[hour].tariff_bdt_per_kwh
        for hour in range(24)
    )
    peak_grid = max(item.grid_kwh for item in response.hourly_plan)
    if not _close(response.total_grid_kwh, total_grid):
        raise PlanValidationError("total_grid_kwh mismatch")
    if not _close(response.total_cost_bdt, total_cost):
        raise PlanValidationError("total_cost_bdt mismatch")
    if not _close(response.peak_grid_kwh, peak_grid):
        raise PlanValidationError("peak_grid_kwh mismatch")
