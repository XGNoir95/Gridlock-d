from typing import Any


def valid_request_payload() -> dict[str, Any]:
    return {
        "scenario_id": "TEST-001",
        "operator_notes": ["The cafeteria menu changes tomorrow."],
        "hours": [
            {
                "hour": hour,
                "demand_kwh": 100,
                "solar_kwh": 0,
                "tariff_bdt_per_kwh": 10,
            }
            for hour in range(24)
        ],
        "battery": {
            "capacity_kwh": 200,
            "initial_energy_kwh": 100,
            "minimum_energy_kwh": 20,
            "max_charge_kwh_per_hour": 50,
            "max_discharge_kwh_per_hour": 50,
        },
    }
