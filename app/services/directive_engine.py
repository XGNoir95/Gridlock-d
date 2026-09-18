"""Compile validated directives into deterministic per-hour constraints."""

from dataclasses import dataclass
from math import isclose

from app.schemas import (
    DirectiveInterpretation,
    GridCapAdjustment,
    HoursAdjustment,
    OptimizeRequest,
    ReserveAdjustment,
    SolarReductionAdjustment,
)


class DirectiveConflictError(ValueError):
    """Raised for a directive combination without defined official semantics."""


@dataclass(frozen=True)
class CompiledDirectives:
    effective_solar: tuple[float, ...]
    active_minimum: tuple[float, ...]
    charge_allowed: tuple[bool, ...]
    discharge_allowed: tuple[bool, ...]
    grid_upper_bound: tuple[float | None, ...]


def compile_directives(
    request: OptimizeRequest,
    directives: list[DirectiveInterpretation],
) -> CompiledDirectives:
    """Apply official directive semantics to per-hour constraint arrays."""
    effective_solar = [entry.solar_kwh for entry in request.hours]
    active_minimum = [request.battery.minimum_energy_kwh] * 24
    charge_allowed = [True] * 24
    discharge_allowed = [True] * 24
    grid_upper_bound: list[float | None] = [None] * 24
    solar_factor: list[float | None] = [None] * 24

    for directive in directives:
        adjustment = directive.structured_adjustment
        if directive.directive_type == "no_op":
            continue
        if not isinstance(adjustment, HoursAdjustment):
            raise DirectiveConflictError("applicable directive is missing hours")

        if directive.directive_type == "solar_reduction":
            if not isinstance(adjustment, SolarReductionAdjustment):
                raise DirectiveConflictError("solar directive has invalid shape")
            for hour in adjustment.hours:
                previous = solar_factor[hour]
                if previous is not None and not isclose(previous, adjustment.factor, abs_tol=1e-9):
                    raise DirectiveConflictError("conflicting solar factors overlap")
                solar_factor[hour] = adjustment.factor
                effective_solar[hour] = request.hours[hour].solar_kwh * adjustment.factor
        elif directive.directive_type == "minimum_battery_reserve":
            if not isinstance(adjustment, ReserveAdjustment):
                raise DirectiveConflictError("reserve directive has invalid shape")
            for hour in adjustment.hours:
                active_minimum[hour] = max(
                    active_minimum[hour], adjustment.minimum_energy_kwh
                )
        elif directive.directive_type == "no_charge_window":
            for hour in adjustment.hours:
                charge_allowed[hour] = False
        elif directive.directive_type == "no_discharge_window":
            for hour in adjustment.hours:
                discharge_allowed[hour] = False
        elif directive.directive_type == "max_grid_window":
            if not isinstance(adjustment, GridCapAdjustment):
                raise DirectiveConflictError("grid-cap directive has invalid shape")
            for hour in adjustment.hours:
                previous = grid_upper_bound[hour]
                grid_upper_bound[hour] = (
                    adjustment.max_grid_kwh
                    if previous is None
                    else min(previous, adjustment.max_grid_kwh)
                )

    return CompiledDirectives(
        effective_solar=tuple(effective_solar),
        active_minimum=tuple(active_minimum),
        charge_allowed=tuple(charge_allowed),
        discharge_allowed=tuple(discharge_allowed),
        grid_upper_bound=tuple(grid_upper_bound),
    )
