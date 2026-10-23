# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative_requirements.py

"""Поколения атомарного каталога, явная очередь и журнал нормативного статуса."""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import islice
from uuid import UUID, uuid4

from pdrd_knowledge_service.application.normative_document_formats import (
    PDF_MIME_TYPE,
)
from pdrd_knowledge_service.application.normative_generation_integrity import (
    NormativeGenerationIntegrityError,
    update_digest,
    validate_generation,
)
from pdrd_knowledge_service.application.normative_preview import verified_preview
from pdrd_knowledge_service.application.normative_requirement_batches import (
    MAX_ATOM_BYTES,
    MAX_BATCH_BYTES,
    WRITE_ATTEMPTS,
    WRITE_BACKOFF_SECONDS,
    NormativeBatchWriteFailedError,
    bounded_requirement_batches,
)
from pdrd_knowledge_service.application.normative_requirement_policy import (
    NormativeRequirementPolicy,
)
from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorage,
)
from pdrd_knowledge_service.application.ports.normative_pdf import NormativePdfExtractor
from pdrd_knowledge_service.application.ports.office_conversion import (
    NormativeOfficeToPdfConverter,
)
from pdrd_knowledge_service.application.ports.persistence import (
    CatalogConnectionLostError,
    NormativeCatalogUnitOfWorkFactory,
)
from pdrd_knowledge_service.application.ports.section_locks import (
    CatalogSectionLocks,
    InMemoryCatalogSectionLocks,
)
from pdrd_knowledge_service.core.observability import log_execution_time
from pdrd_knowledge_service.domain.normative_catalog import CatalogArea, IndexingStatus
from pdrd_knowledge_service.domain.normative_outbox import NormativeOutboxMessage
from pdrd_knowledge_service.domain.normative_requirement_extraction import (
    extract_normative_requirements,
    normative_document_identity,
)
from pdrd_knowledge_service.domain.normative_requirement_support import (
    interpretation_blocked,
    native_rule_supported,
)
from pdrd_knowledge_service.domain.normative_requirements import (
    EXTRACTOR_VERSION,
    NormativeRequirement,
    NormativeSourceFragment,
    NormativeStatusEvidence,
    content_digest,
    requirement_status,
    stable_requirement_id,
)

logger = logging.getLogger(__name__)


class NormativeRequirementsConflictError(RuntimeError):
    """Запрос конфликтует с удалением, поколением или изменением исходного документа."""


class NormativeRequirementsNotFoundError(LookupError):
    """Документ или нужное поколение отсутствует; это не программный KeyError."""


async def _document(uow, document_id: UUID, *, lock=False):
    """Изолирует нормативы от личных пакетов и удаляемых разделов."""
    document = await (
        uow.documents.get_for_update(document_id)
        if lock
        else uow.documents.get(document_id)
    )
    if document is None or document.area is not CatalogArea.NORMATIVE:
        raise NormativeRequirementsNotFoundError("Нормативный документ не найден.")
    section = await uow.sections.get(document.section_id)
    if (
        section is None
        or section.deleting
        or document.index_status is IndexingStatus.DELETING
    ):
        raise NormativeRequirementsConflictError(
            "Нормативный документ или раздел удаляется."
        )
    return document


def _evidence(rows) -> tuple[NormativeStatusEvidence, ...]:
    """Восстанавливает типизированные доказательства immutable snapshot."""
    return tuple(
        NormativeStatusEvidence(**{**row, "clause_scope": tuple(row["clause_scope"])})
        for row in rows
    )


@dataclass(frozen=True, slots=True)
class NormativeRequirementCatalog:
    """Читает все атомы по стабильной пагинации и явно ставит построение в существующий outbox."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory
    extractor_version: str = EXTRACTOR_VERSION
    policy: NormativeRequirementPolicy = field(
        default_factory=NormativeRequirementPolicy
    )

    async def current(self, uow, document):
        """Сверяет SHA, extractor и журнал статуса; repair UUID не меняет идентичность исходника."""
        proofs = [
            item.as_dict()
            for item in await uow.requirements.status_evidence(
                str(document.document_id), document.sha256
            )
        ]
        fingerprint = stable_requirement_id(
            str(document.document_id),
            document.sha256,
            self.extractor_version,
            content_digest(proofs),
        )
        latest = await uow.requirements.latest_generation(
            str(document.document_id), fingerprint=fingerprint
        )
        generation = (
            latest
            if latest
            and latest.get("fingerprint", latest["generation_id"]) == fingerprint
            else await uow.requirements.get_generation(fingerprint)
        )
        return fingerprint, proofs, generation

    @log_execution_time(operation="normative_requirements.index_state")
    async def index_state(self, *, document_id: UUID) -> dict:
        """Показывает очередь, отказ и прежнее рабочее поколение без скрытого построения."""
        async with self.unit_of_work_factory() as uow:
            document = await _document(uow, document_id)
            _, _, requested = await self.current(uow, document)
            active = await uow.requirements.active_generation(str(document_id))
        return {
            "document_id": str(document_id),
            "source_sha256": document.sha256,
            "active_generation": active,
            "requested_generation": requested,
            "state": requested["state"] if requested else "not_indexed",
            "extractor_version": self.extractor_version,
        }

    @log_execution_time(operation="normative_requirements.queue")
    async def queue(
        self,
        *,
        document_id: UUID,
        respect_backoff=False,
        max_in_flight=None,
        restart_exhausted=False,
    ) -> dict:
        """Не затрагивает chunk index и не запускает скрытую переиндексацию при чтении."""
        async with self.unit_of_work_factory() as uow:
            document = await _document(uow, document_id, lock=True)
            generation = await self.stage(
                uow,
                document,
                respect_backoff=respect_backoff,
                max_in_flight=max_in_flight,
                restart_exhausted=restart_exhausted,
            )
            await uow.commit()
            return generation

    async def stage(
        self,
        uow,
        document,
        *,
        respect_backoff=False,
        max_in_flight=None,
        restart_exhausted=False,
    ):
        """Сохраняет намерение в той же транзакции, что READY; commit принадлежит вызывающему коду."""
        if document.index_status is not IndexingStatus.READY:
            raise NormativeRequirementsConflictError(
                "Сначала должен быть готов исходный документ."
            )
        now = datetime.now(UTC)
        fingerprint, proof_rows, existing = await self.current(uow, document)
        for previous in await uow.requirements.unpublished_generations(
            str(document.document_id)
        ):
            if previous.get("fingerprint", previous["generation_id"]) != fingerprint:
                await uow.requirements.save_generation(
                    {
                        **previous,
                        "state": "failed",
                        "active": False,
                        "attempt_id": None,
                        "error_code": "superseded_source_or_status",
                        "error_message": "Изменились SHA, версия extractor или журнал нормативного статуса.",
                        "retryable": False,
                        "updated_at": now.isoformat(),
                    }
                )
        generation_id = existing["generation_id"] if existing else fingerprint
        restart = {}
        if restart_exhausted:
            if (
                not existing
                or existing["state"] != "failed"
                or existing["active"]
                or max(
                    existing.get("attempt_count", 0), existing.get("dispatch_count", 0)
                )
                < self.policy.max_attempts
            ):
                raise NormativeRequirementsConflictError(
                    "Административный повтор разрешён только исчерпанному failed-поколению."
                )
            restart = {
                "previous_generation_id": generation_id,
                "previous_attempt_count": existing.get("attempt_count", 0),
                "previous_dispatch_count": existing.get("dispatch_count", 0),
                "administrative_restart_at": now.isoformat(),
                "administrative_restart_reason": "explicit_exhausted_generation_retry",
            }
            generation_id = stable_requirement_id(
                fingerprint, "admin-retry", str(uuid4())
            )
            existing = None
        if existing and existing["state"] == "ready":
            try:
                await validate_generation(uow.requirements, existing)
            except NormativeGenerationIntegrityError:
                # Повреждённое ready не очищается: строится отдельное repair-поколение.
                generation_id, existing = (
                    stable_requirement_id(fingerprint, "repair", str(uuid4())),
                    None,
                )
            else:
                if not existing["active"]:
                    await uow.requirements.publish(
                        {**existing, "active": True, "updated_at": now.isoformat()}
                    )
                return {**existing, "active": True}
        if existing:
            fresh = datetime.fromisoformat(existing["updated_at"]) > now - timedelta(
                seconds=self.policy.stale_seconds
            )
            if existing["state"] == "building" and fresh:
                raise NormativeRequirementsConflictError(
                    "Поколение уже строится; дождитесь завершения."
                )
            if (
                existing["state"] == "queued"
                and not existing.get("dispatch_pending")
                and (
                    fresh
                    or await uow.outbox.has_pending_requirements(
                        document.document_id, generation_id
                    )
                )
            ):
                return existing
            exhausted = (
                max(existing.get("attempt_count", 0), existing.get("dispatch_count", 0))
                >= self.policy.max_attempts
            )
            if exhausted or (
                respect_backoff
                and (
                    existing.get("retryable") is False
                    or (
                        existing.get("retry_at")
                        and datetime.fromisoformat(existing["retry_at"]) > now
                    )
                )
            ):
                if existing["state"] in {"queued", "building"} and not fresh:
                    existing = {
                        **existing,
                        "state": "failed",
                        "error_code": "attempt_limit_exhausted",
                        "retryable": False,
                        "updated_at": now.isoformat(),
                    }
                    await uow.requirements.save_generation(existing)
                return existing
        await uow.requirements.lock_admission()
        limit = min(
            max_in_flight or self.policy.max_in_flight, self.policy.max_in_flight
        )
        if not 1 <= limit <= 32:
            raise ValueError("max_in_flight должен быть от 1 до 32.")
        pending = (
            await uow.requirements.in_flight(exclude_generation_id=generation_id)
            >= limit
        )
        generation = {
            **(existing or {}),
            **restart,
            "generation_id": generation_id,
            "fingerprint": fingerprint,
            "document_id": str(document.document_id),
            "source_sha256": document.sha256,
            "extractor_version": self.extractor_version,
            "state": "queued",
            "active": False,
            "count": 0,
            "error_code": None,
            "error_message": None,
            "created_at": existing["created_at"] if existing else now.isoformat(),
            "updated_at": now.isoformat(),
            "status_evidence": proof_rows,
            "manifest": {},
            "attempt_id": None,
            "dispatch_pending": pending,
            "attempt_count": existing.get("attempt_count", 0) if existing else 0,
            "dispatch_count": (existing.get("dispatch_count", 0) if existing else 0)
            + (0 if pending else 1),
            "retry_at": None,
            "retryable": True,
        }
        await uow.requirements.save_generation(generation)
        if not pending:
            await uow.outbox.add(
                NormativeOutboxMessage(
                    message_id=uuid4(),
                    aggregate_id=document.document_id,
                    event_type=NormativeOutboxMessage.REQUIREMENTS_REQUESTED_EVENT,
                    payload={
                        "document_id": str(document.document_id),
                        "generation_id": generation_id,
                    },
                    attempt_count=0,
                    last_error=None,
                    created_at=now,
                    published_at=None,
                )
            )
        return generation

    @log_execution_time(operation="normative_requirements.list")
    async def list(
        self,
        *,
        document_id: UUID,
        generation_id: str | None = None,
        offset=0,
        limit=100,
        assessed_on: str | None = None,
    ) -> dict:
        """Возвращает полный каталог, а не долю найденных similarity-фрагментов."""
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("Пагинация: offset >= 0, limit от 1 до 200.")
        on = assessed_on or datetime.now(UTC).date().isoformat()
        async with self.unit_of_work_factory() as uow:
            document = await _document(uow, document_id)
            generation = await (
                uow.requirements.get_generation(generation_id)
                if generation_id
                else uow.requirements.active_generation(str(document_id))
            )
            if generation is None or generation["document_id"] != str(document_id):
                raise NormativeRequirementsNotFoundError(
                    "Опубликованное поколение атомарных нормативов не найдено."
                )
            if generation["state"] != "ready":
                raise NormativeRequirementsConflictError(
                    "Поколение ещё не опубликовано."
                )
            if not generation_id and generation["source_sha256"] != document.sha256:
                raise NormativeRequirementsConflictError(
                    "Исходный документ изменился; требуется явная индексация."
                )
            rows = await uow.requirements.list_requirements(
                generation["generation_id"], offset, limit
            )
            current_proofs = await uow.requirements.status_evidence(
                str(document_id), document.sha256
            )
        proofs = _evidence(generation["status_evidence"])
        status_changed = content_digest(
            [item.as_dict() for item in current_proofs]
        ) != content_digest(generation["status_evidence"])
        requirements = []
        for row in rows:
            typed = NormativeRequirement(
                **{
                    **row,
                    "source_fragments": tuple(
                        NormativeSourceFragment(**part)
                        for part in row["source_fragments"]
                    ),
                }
            )
            status = requirement_status(typed, proofs, on)
            if status_changed:
                status = {
                    **status,
                    "edition_status": "unknown",
                    "requirement_binding": "unknown",
                    "reason_code": "status_evidence_changed",
                }
            requirements.append({**row, "normative_status": status})
        return {
            **generation,
            "requirements": requirements,
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(rows)
            if offset + len(rows) < generation["count"]
            else None,
            "historical_source": generation["source_sha256"] != document.sha256,
            "status_evidence_changed": status_changed,
            "coverage_basis": "complete_atomic_generation"
            if generation.get("manifest", {}).get("coverage") == "complete"
            else "partial_atomic_generation",
            "vlm_calls": 0,
            "embedding_calls": 0,
        }

    @log_execution_time(operation="normative_requirements.record_status")
    async def record_status(
        self, *, document_id: UUID, payload: dict, actor_id: UUID
    ) -> dict:
        """Сохраняет аттестованное оператором доказательство, не делая HTTP-запросов по URL."""
        now = datetime.now(UTC).isoformat()
        async with self.unit_of_work_factory() as uow:
            document = await _document(uow, document_id, lock=True)
            evidence = NormativeStatusEvidence(
                **payload,
                evidence_id=stable_requirement_id(
                    str(document_id), document.sha256, payload, str(actor_id)
                ),
                document_id=str(document_id),
                source_sha256=document.sha256,
                verified_by=str(actor_id),
                recorded_at=now,
            )
            existing = await uow.requirements.status_evidence(
                str(document_id), document.sha256
            )
            for item in existing:
                if item.evidence_id == evidence.evidence_id:
                    return item.as_dict()
            await uow.requirements.add_status_evidence(evidence)
            await uow.commit()
        return evidence.as_dict()

    @log_execution_time(operation="normative_requirements.evidence")
    async def evidence(self, *, document_id: UUID) -> dict:
        """Отдаёт журнал отдельно от готовности индекса, включая неизвестный статус."""
        async with self.unit_of_work_factory() as uow:
            document = await _document(uow, document_id)
            proofs = await uow.requirements.status_evidence(
                str(document_id), document.sha256
            )
        return {
            "document_id": str(document_id),
            "source_sha256": document.sha256,
            "status_evidence": [item.as_dict() for item in proofs],
        }


@dataclass(frozen=True, slots=True)
class BuildNormativeRequirements:
    """Обрабатывает источник порциями без VLM, embedding и изменения Qdrant."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory
    storage: NormativeDocumentStorage
    pdf_extractor: NormativePdfExtractor
    section_locks: CatalogSectionLocks = field(
        default_factory=InMemoryCatalogSectionLocks
    )
    batch_size: int = 20
    office_converter: NormativeOfficeToPdfConverter | None = None
    policy: NormativeRequirementPolicy = field(
        default_factory=NormativeRequirementPolicy
    )

    @log_execution_time(operation="normative_requirements.build")
    async def execute(self, *, document_id: UUID, generation_id: str) -> dict:
        """Повтор worker не публикует частичный каталог и не затирает успешное поколение."""
        if not 1 <= self.batch_size <= 200:
            raise ValueError(
                "Порция атомарной индексации должна содержать от 1 до 200 требований."
            )
        async with self.unit_of_work_factory() as uow:
            document = await _document(uow, document_id)
        async with self.section_locks.shared(document.section_id):
            # Другой namespace ключа: раздел продолжает защищать удаление, документ — попытки worker.
            document_lock = UUID(
                stable_requirement_id("normative-atomic-document", str(document_id))
            )
            async with self.section_locks.exclusive(document_lock):
                return await self._build(document_id, generation_id)

    async def _owned_generation(self, uow, document_id, generation_id, attempt_id=None):
        """Проверяет источник, поколение и актуальную попытку под блокировкой документа."""
        document = await _document(uow, document_id, lock=True)
        generation = await uow.requirements.get_generation(generation_id)
        if (
            generation is None
            or generation["document_id"] != str(document_id)
            or generation["source_sha256"] != document.sha256
        ):
            raise NormativeRequirementsConflictError(
                "Поколение не принадлежит текущему исходному документу."
            )
        if generation["extractor_version"] != EXTRACTOR_VERSION:
            raise NormativeRequirementsConflictError(
                "Версия извлечения worker не соответствует поколению."
            )
        proofs = await uow.requirements.status_evidence(
            str(document_id), document.sha256
        )
        expected = stable_requirement_id(
            str(document_id),
            document.sha256,
            generation["extractor_version"],
            content_digest([item.as_dict() for item in proofs]),
        )
        if generation.get("fingerprint", generation_id) != expected:
            raise NormativeRequirementsConflictError(
                "Журнал нормативного статуса изменился; публикация прежнего snapshot запрещена."
            )
        if attempt_id is not None and (
            generation["attempt_id"] != attempt_id or generation["state"] != "building"
        ):
            raise NormativeRequirementsConflictError(
                "Попытка индексации потеряла право публикации."
            )
        return document, generation

    async def _build(self, document_id, generation_id):
        """Пишет новое поколение, сохраняя старое до атомарного переключения active."""
        attempt_id = str(uuid4())
        async with self.unit_of_work_factory() as uow:
            document, generation = await self._owned_generation(
                uow, document_id, generation_id
            )
            if generation["state"] != "queued" or generation.get("dispatch_pending"):
                return generation
            await uow.requirements.clear_unpublished(generation_id)
            generation = {
                **generation,
                "state": "building",
                "attempt_id": attempt_id,
                "attempt_count": generation.get("attempt_count", 0) + 1,
                "count": 0,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            await uow.requirements.save_generation(generation)
            await uow.commit()
        try:
            original = await self.storage.read(storage_key=document.storage_key)
            if hashlib.sha256(original).hexdigest() != document.sha256:
                raise NormativeRequirementsConflictError(
                    "Содержимое файла не соответствует SHA-256 каталога."
                )
            content = (
                original
                if document.mime_type == PDF_MIME_TYPE
                else await verified_preview(
                    self.storage, self.office_converter, document, original
                )
            )
            pages = await self.pdf_extractor.extract_pages(content=content)
            if not pages:
                raise ValueError("Нормативный PDF не содержит физических страниц.")
            if len({page.page_number for page in pages}) != len(pages) or any(
                page.page_number < 1 for page in pages
            ):
                raise NormativeGenerationIntegrityError(
                    "Неверная нумерация физических страниц."
                )
            code, edition = normative_document_identity(pages)
            empty_pages = [page.page_number for page in pages if not page.text.strip()]
            ocr_pages = {
                page.page_number for page in pages if page.extraction_method == "ocr"
            }
            manifest = {
                "document_code": code,
                "edition": edition,
                "pages": [page.page_number for page in pages],
                "total_pages": len(pages),
                "native_text_pages": len(pages) - len(empty_pages) - len(ocr_pages),
                "ocr_text_pages": sorted(ocr_pages),
                "page_errors": {
                    str(page.page_number): page.extraction_error
                    for page in pages
                    if page.extraction_error
                },
                "source_coverage": "native_and_selective_cpu_ocr"
                if ocr_pages
                else "native_text_only",
                "source_text_empty_pages": empty_pages,
                "coverage": "incomplete" if empty_pages else "complete",
                "coverage_reason": "ocr_required_for_empty_native_pages"
                if empty_pages
                else None,
                "source_sha256": document.sha256,
                "extractor_version": generation["extractor_version"],
                "pdf_sha256": hashlib.sha256(content).hexdigest(),
                "vlm_calls": 0,
                "embedding_calls": 0,
                "write_batch_size": self.batch_size,
                "max_atom_json_bytes": MAX_ATOM_BYTES,
                "max_batch_json_bytes": MAX_BATCH_BYTES,
            }
            async with self.unit_of_work_factory() as uow:
                _, current = await self._owned_generation(
                    uow, document_id, generation_id, attempt_id
                )
                await uow.requirements.save_generation(
                    {**current, "manifest": manifest}
                )
                await uow.commit()
            if len(empty_pages) == len(pages):
                raise ValueError(
                    "ocr_required: все страницы не имеют native text; каталог не опубликован."
                )
            iterator = extract_normative_requirements(
                document_id=document_id,
                source_sha256=document.sha256,
                source_file=document.original_name,
                pages=pages,
                extractor_version=generation["extractor_version"],
            )
            count, supported, unsupported, statistics, digest = (
                0,
                0,
                0,
                {},
                hashlib.sha256(),
            )
            while batch := await asyncio.to_thread(
                lambda: tuple(islice(iterator, self.batch_size))
            ):
                batch = tuple(
                    replace(
                        item,
                        extraction_status="needs_review",
                        limitations=(*item.limitations, "ocr_text_requires_review"),
                    )
                    if any(part.page in ocr_pages for part in item.source_fragments)
                    else item
                    for item in batch
                )
                for portion, payload_bytes in bounded_requirement_batches(batch):
                    generation = await self._persist_batch(
                        document_id,
                        generation_id,
                        attempt_id,
                        count,
                        portion,
                        payload_bytes,
                    )
                    # Счётчик и digest меняются только после commit или его сверки в новой UOW.
                    count += len(portion)
                    for item in portion:
                        update_digest(digest, item.as_dict())
                        supported += int(native_rule_supported(item))
                        unsupported += int(interpretation_blocked(item))
                        for key in (
                            "type:" + item.verification_type,
                            "modality:" + item.modality,
                            "extraction:" + item.extraction_status,
                        ):
                            statistics[key] = statistics.get(key, 0) + 1
            async with self.unit_of_work_factory() as uow:
                _, current = await self._owned_generation(
                    uow, document_id, generation_id, attempt_id
                )
                generation = {
                    **current,
                    "state": "ready",
                    "active": True,
                    "count": count,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "error_code": None,
                    "manifest": {
                        **manifest,
                        "statistics": statistics,
                        "requirements_digest": digest.hexdigest(),
                        "extracted_requirements": count,
                        "requirements_supported": supported,
                        "requirements_unsupported": unsupported,
                        "requirements_support_unassessed": count
                        - supported
                        - unsupported,
                        "support_coverage": "conservative_support_and_extraction_guard_lower_bounds",
                        "actually_checkable": "requires_project_facts_and_normative_status",
                        "interpretation_required": sum(
                            value
                            for key, value in statistics.items()
                            if key == "extraction:needs_review"
                        ),
                    },
                }
                await validate_generation(
                    uow.requirements,
                    generation,
                    pages={page.page_number: page.text for page in pages},
                )
                await uow.requirements.publish(generation)
                await uow.commit()
            return generation
        except Exception as error:
            try:
                await self._record_failure(
                    document_id, generation_id, attempt_id, error
                )
            except CatalogConnectionLostError:
                logger.warning(
                    "normative_failure_record_unavailable generation=%s error_type=%s",
                    generation_id,
                    type(error).__name__,
                )
            raise

    async def _record_failure(self, document_id, generation_id, attempt_id, error):
        """Сохраняет отказ отдельной транзакцией; сбой сервера не маскирует исходную ошибку."""
        async with self.unit_of_work_factory() as uow:
            await uow.documents.get_for_update(document_id)
            current = await uow.requirements.get_generation(generation_id)
            if (
                current
                and current["attempt_id"] == attempt_id
                and current["state"] == "building"
            ):
                await uow.requirements.save_generation(
                    {
                        **current,
                        "state": "failed",
                        "error_code": type(error).__name__,
                        "error_message": str(error)[:2000],
                        "retryable": not isinstance(
                            error, (ValueError, NormativeRequirementsConflictError)
                        ),
                        "retry_at": (
                            datetime.now(UTC)
                            + timedelta(
                                seconds=self.policy.retry_backoff_seconds
                                * current.get("attempt_count", 1)
                            )
                        ).isoformat(),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
                await uow.commit()

    async def _persist_batch(
        self, document_id, generation_id, attempt_id, offset, batch, payload_bytes
    ):
        """Повторяет только неподтверждённую транзакцию; неоднозначный commit сначала сверяет."""
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                async with self.unit_of_work_factory() as uow:
                    _, current = await self._owned_generation(
                        uow, document_id, generation_id, attempt_id
                    )
                    if current["count"] == offset + len(batch):
                        actual = await uow.requirements.list_requirements(
                            generation_id, offset, len(batch)
                        )
                        if content_digest(actual) != content_digest(
                            [item.as_dict() for item in batch]
                        ):
                            raise NormativeGenerationIntegrityError(
                                "Не подтверждено содержимое порции после обрыва commit."
                            )
                        return current
                    if current["count"] != offset:
                        raise NormativeGenerationIntegrityError(
                            "Счётчик порции не соответствует подтверждённому offset."
                        )
                    await uow.requirements.write_batch(generation_id, offset, batch)
                    generation = {
                        **current,
                        "count": offset + len(batch),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                    await uow.requirements.save_generation(generation)
                    await uow.commit()
                logger.info(
                    "normative_batch_committed generation=%s offset=%s rows=%s "
                    "json_bytes=%s attempt=%s duration_ms=%.2f",
                    generation_id,
                    offset,
                    len(batch),
                    payload_bytes,
                    attempt,
                    (time.perf_counter() - started) * 1000,
                )
                return generation
            except CatalogConnectionLostError as error:
                logger.warning(
                    "normative_batch_connection_lost generation=%s offset=%s rows=%s "
                    "json_bytes=%s attempt=%s limit=%s",
                    generation_id,
                    offset,
                    len(batch),
                    payload_bytes,
                    attempt,
                    WRITE_ATTEMPTS,
                )
                if attempt == WRITE_ATTEMPTS:
                    raise NormativeBatchWriteFailedError(
                        f"normative_batch_write_exhausted: generation={generation_id}; offset={offset}; "
                        f"rows={len(batch)}; json_bytes={payload_bytes}; attempts={attempt}. "
                        "Нужна диагностика PostgreSQL/worker до повторного запуска."
                    ) from error
                await asyncio.sleep(WRITE_BACKOFF_SECONDS * 2 ** (attempt - 1))
