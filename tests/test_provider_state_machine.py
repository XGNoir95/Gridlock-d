from collections.abc import Sequence

import pytest

from app.schemas import InterpretationEnvelope, OptimizeRequest
from app.services.interpreter import InterpretationProvider, ModelOutputError, ProviderError
from app.services.planner import Planner, PlanningError
from tests.helpers import valid_request_payload
from tests.test_api import mismatched_envelope, no_op_envelope

Outcome = InterpretationEnvelope | Exception


class ScriptedProvider(InterpretationProvider):
    def __init__(self, outcomes: Sequence[Outcome]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[str | None] = []

    async def generate(
        self,
        request: OptimizeRequest,
        correction: str | None = None,
    ) -> InterpretationEnvelope:
        self.calls.append(correction)
        outcome = self._outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def request() -> OptimizeRequest:
    return OptimizeRequest.model_validate(valid_request_payload())


@pytest.mark.asyncio
async def test_primary_success_uses_one_call_and_never_touches_backup() -> None:
    primary = ScriptedProvider([no_op_envelope()])
    backup = ScriptedProvider([no_op_envelope()])

    await Planner(primary=primary, backup=backup).plan(request())

    assert primary.calls == [None]
    assert backup.calls == []


@pytest.mark.asyncio
async def test_guardrail_invalid_primary_gets_one_primary_repair() -> None:
    primary = ScriptedProvider([mismatched_envelope(), no_op_envelope()])
    backup = ScriptedProvider([no_op_envelope()])

    await Planner(primary=primary, backup=backup).plan(request())

    assert len(primary.calls) == 2
    assert primary.calls[1] is not None
    assert "previous_candidate" in primary.calls[1]
    assert backup.calls == []


@pytest.mark.asyncio
async def test_two_invalid_primary_results_fail_without_backup() -> None:
    primary = ScriptedProvider([mismatched_envelope(), mismatched_envelope()])
    backup = ScriptedProvider([no_op_envelope()])

    with pytest.raises(PlanningError):
        await Planner(primary=primary, backup=backup).plan(request())

    assert len(primary.calls) == 2
    assert backup.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [ProviderError("timeout"), ProviderError("rate limit")],
)
async def test_primary_provider_failure_uses_one_backup_call(failure: ProviderError) -> None:
    primary = ScriptedProvider([failure])
    backup = ScriptedProvider([no_op_envelope()])

    await Planner(primary=primary, backup=backup).plan(request())

    assert primary.calls == [None]
    assert backup.calls == [None]


@pytest.mark.asyncio
async def test_both_provider_failures_stop_after_two_calls() -> None:
    primary = ScriptedProvider([ProviderError("primary")])
    backup = ScriptedProvider([ProviderError("backup")])

    with pytest.raises(PlanningError):
        await Planner(primary=primary, backup=backup).plan(request())

    assert len(primary.calls) == 1
    assert len(backup.calls) == 1


@pytest.mark.asyncio
async def test_invalid_backup_result_never_gets_a_repair() -> None:
    primary = ScriptedProvider([ProviderError("primary")])
    backup = ScriptedProvider([mismatched_envelope(), no_op_envelope()])

    with pytest.raises(PlanningError):
        await Planner(primary=primary, backup=backup).plan(request())

    assert len(primary.calls) == 1
    assert len(backup.calls) == 1


@pytest.mark.asyncio
async def test_parse_failure_consumes_primary_repair_and_never_uses_backup() -> None:
    primary = ScriptedProvider(
        [ModelOutputError("bad schema"), ProviderError("repair transport")]
    )
    backup = ScriptedProvider([no_op_envelope()])

    with pytest.raises(PlanningError):
        await Planner(primary=primary, backup=backup).plan(request())

    assert len(primary.calls) == 2
    assert backup.calls == []


@pytest.mark.asyncio
async def test_insufficient_remaining_budget_prevents_second_call() -> None:
    primary = ScriptedProvider([mismatched_envelope(), no_op_envelope()])
    backup = ScriptedProvider([no_op_envelope()])

    with pytest.raises(PlanningError):
        await Planner(
            primary=primary,
            backup=backup,
            hard_deadline_seconds=0.001,
            minimum_second_attempt_seconds=10,
        ).plan(request())

    assert len(primary.calls) == 1
    assert backup.calls == []
