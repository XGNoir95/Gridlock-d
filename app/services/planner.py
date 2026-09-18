"""End-to-end GridWise planning orchestration."""

import asyncio
import json
import time
from typing import Any

from app.schemas import InterpretationEnvelope, OptimizeRequest, OptimizeResponse
from app.services.directive_engine import DirectiveConflictError, compile_directives
from app.services.guardrails import GuardrailError, validate_interpretation
from app.services.interpreter import InterpretationProvider, ModelOutputError, ProviderError
from app.services.optimizer import OptimizationError, solve_schedule
from app.services.plan_validator import PlanValidationError, validate_plan
from app.services.response_builder import build_response


class PlanningError(RuntimeError):
    """A controlled internal failure that must not expose implementation details."""


class Planner:
    def __init__(
        self,
        primary: InterpretationProvider,
        backup: InterpretationProvider | None = None,
        *,
        hard_deadline_seconds: float = 9.0,
        minimum_second_attempt_seconds: float = 0.25,
    ) -> None:
        self._primary = primary
        self._backup = backup
        self._hard_deadline_seconds = hard_deadline_seconds
        self._minimum_second_attempt_seconds = minimum_second_attempt_seconds

    async def _invoke(
        self,
        provider: InterpretationProvider,
        request: OptimizeRequest,
        deadline: float,
        *,
        correction: str | None = None,
        second_attempt: bool = False,
    ) -> InterpretationEnvelope:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (
            second_attempt and remaining < self._minimum_second_attempt_seconds
        ):
            raise PlanningError("insufficient request budget for model attempt")
        try:
            async with asyncio.timeout(remaining):
                return await provider.generate(request, correction)
        except TimeoutError as exc:
            raise ProviderError("model attempt exceeded request deadline") from exc

    @staticmethod
    def _repair_context(error: Exception, envelope: InterpretationEnvelope | None) -> str:
        context: dict[str, Any] = {"validation_error": str(error)}
        if envelope is not None:
            context["previous_candidate"] = envelope.model_dump(mode="json")
        return json.dumps(context, ensure_ascii=False)

    async def _interpret(self, request: OptimizeRequest, deadline: float) -> list:
        try:
            envelope = await self._invoke(self._primary, request, deadline)
        except ModelOutputError as first_error:
            try:
                repaired = await self._invoke(
                    self._primary,
                    request,
                    deadline,
                    correction=self._repair_context(first_error, None),
                    second_attempt=True,
                )
                return validate_interpretation(request, repaired)
            except (GuardrailError, ModelOutputError, ProviderError, PlanningError) as exc:
                raise PlanningError("model output repair failed") from exc
        except ProviderError as first_error:
            if self._backup is None:
                raise PlanningError("primary model provider unavailable") from first_error
            try:
                backup_result = await self._invoke(
                    self._backup,
                    request,
                    deadline,
                    second_attempt=True,
                )
                return validate_interpretation(request, backup_result)
            except (GuardrailError, ModelOutputError, ProviderError, PlanningError) as exc:
                raise PlanningError("backup model provider failed") from exc

        try:
            return validate_interpretation(request, envelope)
        except GuardrailError as first_error:
            try:
                repaired = await self._invoke(
                    self._primary,
                    request,
                    deadline,
                    correction=self._repair_context(first_error, envelope),
                    second_attempt=True,
                )
                return validate_interpretation(request, repaired)
            except (GuardrailError, ModelOutputError, ProviderError, PlanningError) as exc:
                raise PlanningError("model interpretation repair failed") from exc

    async def plan(self, request: OptimizeRequest) -> OptimizeResponse:
        deadline = time.monotonic() + self._hard_deadline_seconds
        directives = await self._interpret(request, deadline)
        try:
            compiled = compile_directives(request, directives)
            solution = await asyncio.to_thread(solve_schedule, request, compiled)
            response = build_response(request, directives, solution)
            validate_plan(request, directives, compiled, response)
            return response
        except (
            DirectiveConflictError,
            OptimizationError,
            PlanValidationError,
            ValueError,
        ) as exc:
            raise PlanningError("planning pipeline failed") from exc
