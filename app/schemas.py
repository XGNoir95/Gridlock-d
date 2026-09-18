"""Strict transport and internal structured-output schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
HourNumber = Annotated[StrictInt, Field(ge=0, le=23)]


class HourInput(StrictModel):
    hour: HourNumber
    demand_kwh: NonNegative
    solar_kwh: NonNegative
    tariff_bdt_per_kwh: NonNegative


class BatteryInput(StrictModel):
    capacity_kwh: NonNegative
    initial_energy_kwh: NonNegative
    minimum_energy_kwh: NonNegative
    max_charge_kwh_per_hour: NonNegative
    max_discharge_kwh_per_hour: NonNegative

    @model_validator(mode="after")
    def validate_capacity_relationships(self) -> "BatteryInput":
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh must not exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh must not exceed capacity_kwh")
        return self


class OptimizeRequest(StrictModel):
    scenario_id: str
    operator_notes: Annotated[list[str], Field(min_length=1, max_length=3)]
    hours: Annotated[list[HourInput], Field(min_length=24, max_length=24)]
    battery: BatteryInput

    @field_validator("scenario_id")
    @classmethod
    def validate_scenario_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scenario_id must be non-empty")
        return value

    @field_validator("operator_notes")
    @classmethod
    def validate_notes(cls, value: list[str]) -> list[str]:
        if any(not note.strip() for note in value):
            raise ValueError("operator_notes entries must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_and_order_hours(self) -> "OptimizeRequest":
        actual = [entry.hour for entry in self.hours]
        if set(actual) != set(range(24)) or len(actual) != len(set(actual)):
            raise ValueError("hours must contain every unique hour from 0 through 23")
        self.hours = sorted(self.hours, key=lambda entry: entry.hour)
        return self


DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]


class HoursAdjustment(StrictModel):
    hours: list[HourNumber]


class SolarReductionAdjustment(HoursAdjustment):
    factor: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ReserveAdjustment(HoursAdjustment):
    minimum_energy_kwh: NonNegative


class GridCapAdjustment(HoursAdjustment):
    max_grid_kwh: NonNegative


Adjustment = SolarReductionAdjustment | ReserveAdjustment | GridCapAdjustment | HoursAdjustment


class DirectiveInterpretation(StrictModel):
    note_index: Annotated[StrictInt, Field(ge=0)]
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Adjustment | None
    explanation: str

    @field_validator("explanation")
    @classmethod
    def validate_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("explanation must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> "DirectiveInterpretation":
        expected_types: dict[str, type[StrictModel]] = {
            "solar_reduction": SolarReductionAdjustment,
            "minimum_battery_reserve": ReserveAdjustment,
            "no_charge_window": HoursAdjustment,
            "no_discharge_window": HoursAdjustment,
            "max_grid_window": GridCapAdjustment,
        }
        if self.directive_type == "no_op":
            if self.applies or self.structured_adjustment is not None:
                raise ValueError("no_op requires applies=false and null adjustment")
            return self
        if not self.applies:
            raise ValueError("non-no_op directives require applies=true")
        if not isinstance(self.structured_adjustment, expected_types[self.directive_type]):
            raise ValueError("structured_adjustment does not match directive_type")
        return self


class TimeWindow(StrictModel):
    start_hour: HourNumber
    end_hour_exclusive: Annotated[StrictInt, Field(ge=0, le=24)]


NumericBasisKind = Literal[
    "usable_fraction",
    "reduction_fraction",
    "absolute_kwh",
    "capacity_fraction",
    "absolute_grid_kwh",
    "none",
]


class NumericBasis(StrictModel):
    kind: NumericBasisKind
    value: NonNegative | None


class NormalizationTrace(StrictModel):
    note_index: Annotated[StrictInt, Field(ge=0)]
    time_window: TimeWindow | None
    explicit_hours: list[HourNumber] | None
    numeric_basis: NumericBasis | None


class InterpretationEnvelope(StrictModel):
    directive_interpretation: list[DirectiveInterpretation]
    normalization_trace: list[NormalizationTrace]


BatteryAction = Literal["charge", "discharge", "idle"]


class HourPlan(StrictModel):
    hour: HourNumber
    grid_kwh: NonNegative
    solar_used_kwh: NonNegative
    battery_action: BatteryAction
    battery_kwh: NonNegative
    battery_energy_after_kwh: NonNegative


class OptimizeResponse(StrictModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: Annotated[list[HourPlan], Field(min_length=24, max_length=24)]
    total_grid_kwh: NonNegative
    total_cost_bdt: NonNegative
    peak_grid_kwh: NonNegative
    plan_summary: str
