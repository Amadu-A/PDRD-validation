# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/restart_blocked_normative_requirements.py

"""Административный повтор одного неисчерпанного, но невозобновляемого failed-поколения."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    NormativeRequirementCatalog,
    NormativeRequirementsConflictError,
    _document,
)
from pdrd_knowledge_service.core.observability import log_execution_time
from pdrd_knowledge_service.domain.normative_catalog import IndexingStatus
from pdrd_knowledge_service.domain.normative_outbox import NormativeOutboxMessage
from pdrd_knowledge_service.domain.normative_requirements import stable_requirement_id


@dataclass(frozen=True, slots=True)
class RestartBlockedNormativeRequirements:
    """Создаёт изолированное поколение; не изменяет старые атомы и автоматический retry."""

    catalog: NormativeRequirementCatalog

    @log_execution_time(operation="normative_requirements.restart_blocked")
    async def execute(self, *, document_id: UUID, expected_generation_id: UUID) -> dict:
        """Под блокировкой документа проверяет точную failed-версию и фиксирует outbox."""
        async with self.catalog.unit_of_work_factory() as uow:
            document = await _document(uow, document_id, lock=True)
            if document.index_status is not IndexingStatus.READY:
                raise NormativeRequirementsConflictError(
                    "Исходный нормативный документ не готов к индексации."
                )

            fingerprint, proofs, previous = await self.catalog.current(uow, document)
            if (
                previous is None
                or previous["generation_id"] != str(expected_generation_id)
                or previous["state"] != "failed"
                or previous["active"]
                or previous.get("retryable", True) is not False
                or max(
                    previous.get("attempt_count", 0),
                    previous.get("dispatch_count", 0),
                )
                >= self.catalog.policy.max_attempts
            ):
                raise NormativeRequirementsConflictError(
                    "Административный повтор возможен только для указанного "
                    "неисчерпанного failed-поколения с retryable=false."
                )

            if await uow.requirements.active_generation(str(document_id)) is not None:
                raise NormativeRequirementsConflictError(
                    "Для документа уже есть опубликованное поколение."
                )
            if (
                previous.get("count", 0) != 0
                or await uow.requirements.count_requirements(previous["generation_id"])
                != 0
            ):
                raise NormativeRequirementsConflictError(
                    "У failed-поколения есть записи; требуется отдельная проверка."
                )

            await uow.requirements.lock_admission()
            if await uow.requirements.in_flight() != 0:
                raise NormativeRequirementsConflictError(
                    "Для административного повтора дождитесь окончания других построений."
                )

            now = datetime.now(UTC)
            generation_id = stable_requirement_id(
                fingerprint, "admin-retry", str(uuid4())
            )
            generation = {
                "generation_id": generation_id,
                "fingerprint": fingerprint,
                "document_id": str(document_id),
                "source_sha256": document.sha256,
                "extractor_version": self.catalog.extractor_version,
                "state": "queued",
                "active": False,
                "count": 0,
                "error_code": None,
                "error_message": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status_evidence": proofs,
                "manifest": {},
                "attempt_id": None,
                "dispatch_pending": False,
                "attempt_count": 0,
                "dispatch_count": 1,
                "retry_at": None,
                "retryable": True,
                "previous_generation_id": previous["generation_id"],
                "previous_attempt_count": previous.get("attempt_count", 0),
                "previous_dispatch_count": previous.get("dispatch_count", 0),
                "administrative_restart_at": now.isoformat(),
                "administrative_restart_reason": "explicit_non_retryable_generation_retry",
            }
            await uow.requirements.save_generation(generation)
            await uow.outbox.add(
                NormativeOutboxMessage(
                    message_id=uuid4(),
                    aggregate_id=document_id,
                    event_type=NormativeOutboxMessage.REQUIREMENTS_REQUESTED_EVENT,
                    payload={
                        "document_id": str(document_id),
                        "generation_id": generation_id,
                    },
                    attempt_count=0,
                    last_error=None,
                    created_at=now,
                    published_at=None,
                )
            )
            await uow.commit()
            return generation
