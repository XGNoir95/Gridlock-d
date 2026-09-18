"""Deterministic linear-program schedule optimizer."""

from dataclasses import dataclass
from math import isfinite

import numpy as np
from scipy.optimize import linprog

from app.schemas import OptimizeRequest
from app.services.directive_engine import CompiledDirectives

HOURS = 24
GRID_OFFSET = 0
SOLAR_OFFSET = HOURS
BATTERY_OFFSET = HOURS * 2
ENERGY_OFFSET = HOURS * 3
VARIABLES = HOURS * 4


class OptimizationError(RuntimeError):
    """Raised when the solver cannot produce a finite optimal plan."""


@dataclass(frozen=True)
class OptimizationSolution:
    grid: tuple[float, ...]
    solar_used: tuple[float, ...]
    battery_flow: tuple[float, ...]
    battery_energy_after: tuple[float, ...]


def solve_schedule(
    request: OptimizeRequest,
    compiled: CompiledDirectives,
) -> OptimizationSolution:
    """Solve the official 24-hour cost-minimization LP."""
    objective = np.zeros(VARIABLES)
    for hour in range(HOURS):
        objective[GRID_OFFSET + hour] = request.hours[hour].tariff_bdt_per_kwh

    rows: list[np.ndarray] = []
    right_hand_side: list[float] = []
    for hour in range(HOURS):
        balance = np.zeros(VARIABLES)
        balance[GRID_OFFSET + hour] = 1
        balance[SOLAR_OFFSET + hour] = 1
        balance[BATTERY_OFFSET + hour] = -1
        rows.append(balance)
        right_hand_side.append(request.hours[hour].demand_kwh)

        state = np.zeros(VARIABLES)
        state[ENERGY_OFFSET + hour] = 1
        state[BATTERY_OFFSET + hour] = -1
        if hour == 0:
            state_rhs = request.battery.initial_energy_kwh
        else:
            state[ENERGY_OFFSET + hour - 1] = -1
            state_rhs = 0
        rows.append(state)
        right_hand_side.append(state_rhs)

    neutrality = np.zeros(VARIABLES)
    neutrality[ENERGY_OFFSET + HOURS - 1] = 1
    rows.append(neutrality)
    right_hand_side.append(request.battery.initial_energy_kwh)

    bounds: list[tuple[float | None, float | None]] = []
    bounds.extend((0, compiled.grid_upper_bound[hour]) for hour in range(HOURS))
    bounds.extend((0, compiled.effective_solar[hour]) for hour in range(HOURS))
    for hour in range(HOURS):
        lower = -request.battery.max_discharge_kwh_per_hour
        upper = request.battery.max_charge_kwh_per_hour
        if not compiled.discharge_allowed[hour]:
            lower = 0
        if not compiled.charge_allowed[hour]:
            upper = 0
        bounds.append((lower, upper))
    bounds.extend(
        (compiled.active_minimum[hour], request.battery.capacity_kwh)
        for hour in range(HOURS)
    )

    result = linprog(
        objective,
        A_eq=np.vstack(rows),
        b_eq=np.asarray(right_hand_side),
        bounds=bounds,
        method="highs",
        options={"time_limit": 2.0},
    )
    if not result.success or result.x is None:
        status = "infeasible" if result.status == 2 else "not optimal"
        raise OptimizationError(f"optimization {status}")
    if not all(isfinite(float(value)) for value in result.x):
        raise OptimizationError("optimization returned non-finite values")

    values = result.x
    return OptimizationSolution(
        grid=tuple(float(value) for value in values[GRID_OFFSET:SOLAR_OFFSET]),
        solar_used=tuple(float(value) for value in values[SOLAR_OFFSET:BATTERY_OFFSET]),
        battery_flow=tuple(float(value) for value in values[BATTERY_OFFSET:ENERGY_OFFSET]),
        battery_energy_after=tuple(float(value) for value in values[ENERGY_OFFSET:VARIABLES]),
    )
