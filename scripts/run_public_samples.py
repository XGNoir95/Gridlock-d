"""Run the official public samples against a live service and replay responses."""

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

from app.schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse
from app.services.directive_engine import compile_directives
from app.services.plan_validator import validate_plan


def _semantic_view(entries: list[DirectiveInterpretation]) -> list[dict[str, Any]]:
    return [
        {
            "note_index": item.note_index,
            "applies": item.applies,
            "directive_type": item.directive_type,
            "structured_adjustment": (
                item.structured_adjustment.model_dump()
                if item.structured_adjustment is not None
                else None
            ),
        }
        for item in entries
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", type=Path, help="Path to the official sample-cases JSON")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    cases = json.loads(args.samples.read_text(encoding="utf-8"))["cases"]
    endpoint = f"{args.base_url.rstrip('/')}/optimize-energy"

    with httpx.Client(timeout=30) as client:
        for case in cases:
            result = client.post(endpoint, json=case["input"])
            result.raise_for_status()
            request = OptimizeRequest.model_validate(case["input"])
            response = OptimizeResponse.model_validate(result.json())
            expected = [
                DirectiveInterpretation.model_validate(item)
                for item in case["expected_output"]["directive_interpretation"]
            ]
            if _semantic_view(response.directive_interpretation) != _semantic_view(expected):
                raise SystemExit(f"{case['id']}: interpretation mismatch")
            compiled = compile_directives(request, expected)
            ground_truth_response = response.model_copy(
                update={"directive_interpretation": expected}
            )
            validate_plan(request, expected, compiled, ground_truth_response)
            expected_cost = float(case["expected_output"]["total_cost_bdt"])
            if abs(response.total_cost_bdt - expected_cost) > 0.01:
                raise SystemExit(f"{case['id']}: cost mismatch")
            print(f"{case['id']}: pass")


if __name__ == "__main__":
    main()
