# services/knowledge-service/tests/unit/test_restart_blocked_normative_requirements.py

"""Регрессия ручного восстановления без изменения failed-истории и ready-документов."""

import copy
from uuid import UUID, uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
    NormativeRequirementsConflictError,
)
from pdrd_knowledge_service.application.use_cases.recover_normative_requirements import (
    RecoverNormativeRequirements,
)
from pdrd_knowledge_service.application.use_cases.restart_blocked_normative_requirements import (
    RestartBlockedNormativeRequirements,
)
from pdrd_knowledge_service.transport.cli.restart_blocked_normative_requirements import (
    parser,
)

from ..atomic_normative_support import (
    DOCUMENT_ID,
    Extractor,
    State,
    Storage,
    UnitOfWork,
)


async def seed_failure(
    state, factory, *, retryable=False, attempts=1, state_name="failed"
):
    """Имитирует записанный worker отказ, не подменяя текущий fingerprint."""
    queued = await NormativeRequirementCatalog(factory).queue(document_id=DOCUMENT_ID)
    state.generations[queued["generation_id"]].update(
        state=state_name,
        retryable=retryable,
        attempt_count=attempts,
        dispatch_count=1,
        error_code="NormativeAtomTooLargeError",
        error_message="normative_atom_too_large: старая слишком большая статья",
        dispatch_pending=False,
    )
    return queued["generation_id"]


async def test_manual_restart_preserves_failed_history_and_publishes_new_generation():
    """Повтор non-retryable после одной попытки создаёт новый UUID и полный ready."""
    state = State()

    def factory():
        """Открывает новую тестовую транзакцию."""
        return UnitOfWork(state)

    old_id = await seed_failure(state, factory)
    old = copy.deepcopy(state.generations[old_id])
    catalog = NormativeRequirementCatalog(factory)

    result = await RestartBlockedNormativeRequirements(catalog).execute(
        document_id=DOCUMENT_ID,
        expected_generation_id=UUID(old_id),
    )
    new_id = result["generation_id"]
    assert new_id != old_id
    assert result["previous_generation_id"] == old_id
    assert result["previous_attempt_count"] == 1
    assert result["previous_dispatch_count"] == 1
    assert result["administrative_restart_reason"] == (
        "explicit_non_retryable_generation_retry"
    )
    assert result["state"] == "queued"
    assert result["attempt_count"] == 0
    assert result["dispatch_count"] == 1
    assert state.generations[old_id] == old
    assert any(item.payload["generation_id"] == new_id for item in state.outbox)

    ready = await BuildNormativeRequirements(
        factory, Storage(), Extractor(), batch_size=100
    ).execute(document_id=DOCUMENT_ID, generation_id=new_id)
    assert ready["state"] == "ready"
    assert ready["count"] > 0
    assert state.generations[old_id] == old
    report = await RecoverNormativeRequirements(catalog).inspect(
        document_id=DOCUMENT_ID
    )
    row = report["documents"][0]
    assert row["atomic_state"] == "ready"
    assert row["integrity"] == "verified"
    assert row["requirements"] == row["actual_requirements"] == ready["count"]
    assert row["requirements_digest"] == row["verified_requirements_digest"]

    with pytest.raises(NormativeRequirementsConflictError):
        await RestartBlockedNormativeRequirements(catalog).execute(
            document_id=DOCUMENT_ID,
            expected_generation_id=UUID(old_id),
        )


@pytest.mark.parametrize(
    ("retryable", "attempts", "state_name"),
    [
        (True, 1, "failed"),
        (False, 3, "failed"),
        (False, 1, "queued"),
        (False, 1, "building"),
        (False, 1, "ready"),
    ],
)
async def test_manual_restart_rejects_other_states_without_changes(
    retryable, attempts, state_name
):
    """Не обходит авто retry, лимит, in-flight и ready состояния."""
    state = State()

    def factory():
        """Открывает новую тестовую транзакцию."""
        return UnitOfWork(state)

    old_id = await seed_failure(
        state, factory, retryable=retryable, attempts=attempts, state_name=state_name
    )
    snapshot = copy.deepcopy(state.generations)
    count = len(state.outbox)
    with pytest.raises(NormativeRequirementsConflictError):
        await RestartBlockedNormativeRequirements(
            NormativeRequirementCatalog(factory)
        ).execute(document_id=DOCUMENT_ID, expected_generation_id=UUID(old_id))
    assert state.generations == snapshot
    assert len(state.outbox) == count


async def test_manual_restart_rejects_wrong_generation_and_parallel_work():
    """CAS по UUID и глобальный admission исключают нецелевой запуск."""
    state = State()

    def factory():
        """Открывает новую тестовую транзакцию."""
        return UnitOfWork(state)

    old_id = await seed_failure(state, factory)
    command = RestartBlockedNormativeRequirements(NormativeRequirementCatalog(factory))
    with pytest.raises(NormativeRequirementsConflictError):
        await command.execute(document_id=DOCUMENT_ID, expected_generation_id=uuid4())
    other_id = str(uuid4())
    state.generations[other_id] = {
        **copy.deepcopy(state.generations[old_id]),
        "generation_id": other_id,
        "document_id": str(uuid4()),
        "state": "building",
        "dispatch_pending": False,
    }
    with pytest.raises(NormativeRequirementsConflictError, match="других построений"):
        await command.execute(
            document_id=DOCUMENT_ID, expected_generation_id=UUID(old_id)
        )
    assert state.generations[old_id]["state"] == "failed"


def test_manual_cli_requires_exact_document_and_generation_and_report():
    """Оператор не может запустить всю библиотеку одной командой."""
    cli = parser()
    with pytest.raises(SystemExit):
        cli.parse_args(["--document-id", str(DOCUMENT_ID)])
    with pytest.raises(SystemExit):
        cli.parse_args(["--all-sections", "--report", "report.json"])
    parsed = cli.parse_args(
        [
            "--document-id",
            str(DOCUMENT_ID),
            "--expected-generation-id",
            str(uuid4()),
            "--report",
            "report.json",
        ]
    )
    assert parsed.document_id == DOCUMENT_ID
