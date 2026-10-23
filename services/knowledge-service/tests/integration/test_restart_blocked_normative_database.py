# services/knowledge-service/tests/integration/test_restart_blocked_normative_database.py

"""Изолированный PostgreSQL: транзакционный перезапуск non-retryable поколения."""

import os
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
)
from pdrd_knowledge_service.application.use_cases.recover_normative_requirements import (
    RecoverNormativeRequirements,
)
from pdrd_knowledge_service.application.use_cases.restart_blocked_normative_requirements import (
    RestartBlockedNormativeRequirements,
)

from ..atomic_normative_support import Extractor, Storage, document
from .test_normative_atomic_database import atomic_database as atomic_database

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Нужна изолированная Knowledge PostgreSQL DB.",
    ),
]


async def test_blocked_generation_restart_is_transactional_and_historical(
    atomic_database,
):
    """Старый failed immutable, outbox создаётся, новый ready проходит digest audit."""
    _, factory, section_id = atomic_database
    identifier = uuid4()
    async with factory() as uow:
        await uow.documents.add(
            replace(document(), document_id=identifier, section_id=section_id)
        )
        await uow.commit()

    catalog = NormativeRequirementCatalog(factory)
    queued = await catalog.queue(document_id=identifier)
    old_id = queued["generation_id"]
    async with factory() as uow:
        failed = {
            **queued,
            "state": "failed",
            "active": False,
            "attempt_count": 1,
            "dispatch_count": 1,
            "retryable": False,
            "error_code": "NormativeAtomTooLargeError",
            "error_message": "normative_atom_too_large: sample failure",
        }
        await uow.requirements.save_generation(failed)
        await uow.commit()

    restart = await RestartBlockedNormativeRequirements(catalog).execute(
        document_id=identifier,
        expected_generation_id=UUID(old_id),
    )
    new_id = restart["generation_id"]
    assert new_id != old_id
    assert restart["previous_generation_id"] == old_id
    async with factory() as uow:
        assert await uow.requirements.get_generation(old_id) == failed
        saved = await uow.requirements.get_generation(new_id)
        assert saved["state"] == "queued"
        assert saved["attempt_count"] == 0
        assert saved["previous_attempt_count"] == 1
        pending = await uow.outbox.get_pending(limit=100)
        assert any(item.payload.get("generation_id") == new_id for item in pending)

    ready = await BuildNormativeRequirements(
        factory, Storage(), Extractor(), batch_size=100
    ).execute(document_id=identifier, generation_id=new_id)
    assert ready["state"] == "ready" and ready["count"] > 0
    async with factory() as uow:
        assert await uow.requirements.get_generation(old_id) == failed
    report = await RecoverNormativeRequirements(catalog).inspect(document_id=identifier)
    row = report["documents"][0]
    assert row["integrity"] == "verified"
    assert row["requirements"] == row["actual_requirements"] == ready["count"]
    assert row["requirements_digest"] == row["verified_requirements_digest"]
