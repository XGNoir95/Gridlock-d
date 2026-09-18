"""Deterministic validation for untrusted model interpretations."""

from math import isclose

from app.schemas import (
    DirectiveInterpretation,
    GridCapAdjustment,
    HoursAdjustment,
    InterpretationEnvelope,
    OptimizeRequest,
    ReserveAdjustment,
    SolarReductionAdjustment,
    TimeWindow,
)

TOLERANCE = 0.01


class GuardrailError(ValueError):
    """Raised when structured model output violates deterministic guardrails."""


def _expand_window(window: TimeWindow) -> list[int]:
    start = window.start_hour
    end = window.end_hour_exclusive
    if end == 24:
        hours = list(range(start, 24))
    elif start < end:
        hours = list(range(start, end))
    elif start > end:
        hours = [*range(start, 24), *range(0, end)]
    else:
        raise GuardrailError("time window must affect at least one hour")
    return sorted(set(hours))


def _validate_hours(adjustment: HoursAdjustment) -> None:
    hours = adjustment.hours
    if not hours:
        raise GuardrailError("hours must be non-empty")
    if hours != sorted(hours) or len(hours) != len(set(hours)):
        raise GuardrailError("hours must be unique and ascending")


def _require_close(actual: float, expected: float, label: str) -> None:
    if not isclose(actual, expected, rel_tol=0, abs_tol=TOLERANCE):
        raise GuardrailError(f"{label} disagrees with numeric basis")


def validate_interpretation(
    request: OptimizeRequest,
    envelope: InterpretationEnvelope,
) -> list[DirectiveInterpretation]:
    """Validate model output and return only official directive entries."""
    count = len(request.operator_notes)
    expected_indices = list(range(count))
    directives = envelope.directive_interpretation
    traces = envelope.normalization_trace
    if [item.note_index for item in directives] != expected_indices:
        raise GuardrailError("directive note_index values must be ordered and complete")
    if [item.note_index for item in traces] != expected_indices:
        raise GuardrailError("trace note_index values must be ordered and complete")

    for directive, trace in zip(directives, traces, strict=True):
        adjustment = directive.structured_adjustment
        if directive.directive_type == "no_op":
            if (
                trace.time_window is not None
                or trace.explicit_hours is not None
                or trace.numeric_basis is not None
            ):
                raise GuardrailError("no_op trace must be null")
            continue

        if not isinstance(adjustment, HoursAdjustment):
            raise GuardrailError("applicable directives require hours")
        _validate_hours(adjustment)
        if (trace.time_window is None) == (trace.explicit_hours is None):
            raise GuardrailError("trace requires exactly one time representation")
        traced_hours = (
            _expand_window(trace.time_window)
            if trace.time_window is not None
            else trace.explicit_hours
        )
        if traced_hours is None or traced_hours != sorted(set(traced_hours)):
            raise GuardrailError("explicit trace hours must be unique and ascending")
        if adjustment.hours != traced_hours:
            raise GuardrailError("directive hours disagree with time trace")

        basis = trace.numeric_basis
        if basis is None:
            raise GuardrailError("applicable directives require a numeric basis")

        match directive.directive_type:
            case "solar_reduction":
                if not isinstance(adjustment, SolarReductionAdjustment):
                    raise GuardrailError("solar adjustment has the wrong shape")
                if basis.value is None or basis.kind not in {
                    "usable_fraction",
                    "reduction_fraction",
                }:
                    raise GuardrailError("solar directive has an invalid numeric basis")
                if basis.value > 1:
                    raise GuardrailError("solar fraction must be within [0,1]")
                expected = basis.value if basis.kind == "usable_fraction" else 1 - basis.value
                _require_close(adjustment.factor, expected, "solar factor")
            case "minimum_battery_reserve":
                if not isinstance(adjustment, ReserveAdjustment):
                    raise GuardrailError("reserve adjustment has the wrong shape")
                if basis.value is None or basis.kind not in {"absolute_kwh", "capacity_fraction"}:
                    raise GuardrailError("reserve directive has an invalid numeric basis")
                if basis.kind == "capacity_fraction":
                    if basis.value > 1:
                        raise GuardrailError("capacity fraction must be within [0,1]")
                    expected = request.battery.capacity_kwh * basis.value
                else:
                    expected = basis.value
                _require_close(adjustment.minimum_energy_kwh, expected, "reserve value")
                if adjustment.minimum_energy_kwh > request.battery.capacity_kwh:
                    raise GuardrailError("reserve value exceeds battery capacity")
            case "max_grid_window":
                if not isinstance(adjustment, GridCapAdjustment):
                    raise GuardrailError("grid-cap adjustment has the wrong shape")
                if basis.kind != "absolute_grid_kwh" or basis.value is None:
                    raise GuardrailError("grid cap has an invalid numeric basis")
                _require_close(adjustment.max_grid_kwh, basis.value, "grid cap")
            case "no_charge_window" | "no_discharge_window":
                if basis.kind != "none" or basis.value is not None:
                    raise GuardrailError("battery outage requires numeric basis 'none'")
            case _:
                raise GuardrailError("unsupported directive type")

    return directives
