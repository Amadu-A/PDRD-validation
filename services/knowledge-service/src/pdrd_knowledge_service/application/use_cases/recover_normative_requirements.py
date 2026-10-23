# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/recover_normative_requirements.py

"""Read-only аудит и ограниченное восстановление атомарного каталога из PostgreSQL."""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pdrd_knowledge_service.application.normative_generation_integrity import (
    NormativeGenerationIntegrityError,
    validate_generation,
)
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    NormativeRequirementCatalog,
    NormativeRequirementsConflictError,
    NormativeRequirementsNotFoundError,
)
from pdrd_knowledge_service.domain.normative_catalog import CatalogArea, IndexingStatus


@dataclass(slots=True)
class RecoverNormativeRequirements:
    """Не читает Qdrant, не извлекает PDF и не обращается к embedding/VLM."""

    catalog: NormativeRequirementCatalog
    audit_cursor: int = 0

    async def inspect(
        self,
        *,
        section_id: UUID | None = None,
        document_id: UUID | None = None,
        verify=True,
    ):
        """Динамически перечисляет разделы, включая пустые; никаких записей при dry-run/status."""
        now, documents, sections = datetime.now(UTC), [], []
        async with self.catalog.unit_of_work_factory() as uow:
            all_sections = await uow.sections.list_all()
            all_documents = await uow.documents.list_all()
            selected = {
                str(item.section_id): item
                for item in all_sections
                if section_id is None or item.section_id == section_id
            }
            if section_id is not None and not selected:
                raise NormativeRequirementsNotFoundError("Раздел не найден.")
            found_document = document_id is None
            for document in all_documents:
                if (
                    document.area is not CatalogArea.NORMATIVE
                    or str(document.section_id) not in selected
                    or (document_id is not None and document.document_id != document_id)
                ):
                    continue
                found_document = True
                section = selected[str(document.section_id)]
                _, proofs, generation = await self.catalog.current(uow, document)
                previous = generation or await uow.requirements.latest_generation(
                    str(document.document_id)
                )
                active = await uow.requirements.active_generation(
                    str(document.document_id)
                )
                manifest = generation.get("manifest", {}) if generation else {}
                if not isinstance(manifest, dict):
                    manifest = {}
                state = generation["state"] if generation else "not_indexed"
                reason, integrity, actual = None, "not_checked", 0
                verified_digest = None
                if generation:
                    actual = await uow.requirements.count_requirements(
                        generation["generation_id"]
                    )
                if (
                    document.index_status is not IndexingStatus.READY
                    or section.deleting
                ):
                    reason = (
                        "section_deleting"
                        if section.deleting
                        else "chunk_index_not_ready"
                    )
                elif generation and state == "ready":
                    if verify:
                        try:
                            validated = await validate_generation(
                                uow.requirements, generation
                            )
                            verified_digest = validated["requirements_digest"]
                            integrity = "verified"
                        except NormativeGenerationIntegrityError as error:
                            integrity, reason, state = "corrupt", str(error), "corrupt"
                    elif actual != generation["count"] or not manifest.get(
                        "requirements_digest"
                    ):
                        integrity, reason, state = (
                            "corrupt",
                            "manifest_or_row_count_mismatch",
                            "corrupt",
                        )
                    if state == "ready" and manifest.get("coverage") != "complete":
                        reason = (
                            manifest.get("coverage_reason")
                            or "source_coverage_incomplete"
                        )
                elif generation and state == "failed":
                    reason = generation.get("error_message") or generation.get(
                        "error_code"
                    )
                stale = bool(
                    generation
                    and datetime.fromisoformat(generation["updated_at"])
                    <= now - timedelta(seconds=self.catalog.policy.stale_seconds)
                )
                exhausted = bool(
                    generation
                    and max(
                        generation.get("attempt_count", 0),
                        generation.get("dispatch_count", 0),
                    )
                    >= self.catalog.policy.max_attempts
                )
                retry_due = (
                    not generation
                    or not generation.get("retry_at")
                    or datetime.fromisoformat(generation["retry_at"]) <= now
                )
                eligible = (
                    document.index_status is IndexingStatus.READY
                    and not section.deleting
                    and (
                        state in {"not_indexed", "corrupt"}
                        or (
                            state == "queued"
                            and (generation.get("dispatch_pending") or stale)
                        )
                        or (state == "building" and stale)
                        or (
                            state == "failed"
                            and not exhausted
                            and retry_due
                            and generation.get("retryable", True)
                        )
                    )
                )
                documents.append(
                    {
                        "document_id": str(document.document_id),
                        "section_id": str(document.section_id),
                        "name": document.original_name,
                        "source_sha256": document.sha256,
                        "search_state": document.index_status.value,
                        "atomic_state": state,
                        "generation_id": generation["generation_id"]
                        if generation
                        else None,
                        "intent_exists": previous is not None,
                        "superseded_in_flight": bool(
                            not generation
                            and previous
                            and previous["state"] in {"queued", "building"}
                            and not previous.get("dispatch_pending")
                        ),
                        "active_generation_id": active["generation_id"]
                        if active
                        else None,
                        "extractor_version": self.catalog.extractor_version,
                        "requirements": generation.get("count", 0) if generation else 0,
                        "actual_requirements": actual,
                        "requirements_digest": manifest.get("requirements_digest"),
                        "verified_requirements_digest": verified_digest,
                        "integrity": integrity,
                        "source_coverage": manifest.get("coverage", "unavailable"),
                        "native_text_pages": manifest.get("native_text_pages", 0),
                        "ocr_text_pages": manifest.get("ocr_text_pages", []),
                        "page_errors": manifest.get("page_errors", {}),
                        "total_pages": manifest.get("total_pages", 0),
                        "missing_text_pages": manifest.get(
                            "source_text_empty_pages", []
                        ),
                        "statistics": manifest.get("statistics", {}),
                        "checkable_requirements": manifest.get(
                            "requirements_supported", 0
                        ),
                        "requirements_supported": manifest.get(
                            "requirements_supported", 0
                        ),
                        "requirements_unsupported": manifest.get(
                            "requirements_unsupported",
                            generation.get("count", 0) if generation else 0,
                        ),
                        "actually_checkable": "requires_project_facts_and_normative_status",
                        "requirements_support_unassessed": manifest.get(
                            "requirements_support_unassessed", 0
                        ),
                        "support_coverage": manifest.get(
                            "support_coverage", "not_assessed"
                        ),
                        "checkability_basis": "supported_native_numeric_grammar; applicability_to_project_not_assessed",
                        "legal_status": "evidence_recorded_requires_clause_assessment"
                        if proofs
                        else "unknown",
                        "status_evidence_count": len(proofs),
                        "reason": reason,
                        "dispatch_pending": bool(
                            generation and generation.get("dispatch_pending")
                        ),
                        "attempt_count": generation.get("attempt_count", 0)
                        if generation
                        else 0,
                        "retry_exhausted": exhausted,
                        "stale": stale,
                        "eligible": eligible,
                        "retryable": generation.get("retryable", True)
                        if generation
                        else True,
                        "retry_at": generation.get("retry_at") if generation else None,
                    }
                )
            if not found_document:
                raise NormativeRequirementsNotFoundError(
                    "Нормативный документ не найден в выбранной области."
                )
            for identifier, section in selected.items():
                rows = [row for row in documents if row["section_id"] == identifier]
                sections.append(
                    {
                        "section_id": identifier,
                        "name": section.name,
                        "documents": len(rows),
                        "search_ready": sum(
                            row["search_state"] == "ready" for row in rows
                        ),
                        "atomic_ready": sum(
                            row["atomic_state"] == "ready" for row in rows
                        ),
                        "atomic_complete": sum(
                            row["atomic_state"] == "ready"
                            and row["source_coverage"] == "complete"
                            for row in rows
                        ),
                        "requirements": sum(row["requirements"] for row in rows),
                        "checkable_requirements": sum(
                            row["checkable_requirements"] for row in rows
                        ),
                        "unknown_legal_status": sum(
                            row["legal_status"] == "unknown" for row in rows
                        ),
                    }
                )
            in_flight = await uow.requirements.in_flight()
        return {
            "schema": "normative-requirements-recovery/v1",
            "generated_at": now.isoformat(),
            "sections": sections,
            "documents": documents,
            "states": dict(Counter(row["atomic_state"] for row in documents)),
            "in_flight": in_flight,
            "max_in_flight": self.catalog.policy.max_in_flight,
            "eligible": sum(row["eligible"] for row in documents),
            "vlm_calls": 0,
            "embedding_calls": 0,
            "qdrant_mutations": 0,
        }

    async def execute(
        self,
        *,
        section_id=None,
        document_id=None,
        limit=2,
        max_in_flight=None,
        retry_failed=True,
        only_pending=False,
        inspection=None,
    ):
        """Одна ограниченная итерация; queued-намерения обслуживаются раньше нового backfill."""
        if not 1 <= limit <= 200:
            raise ValueError("Размер reconciliation batch должен быть от 1 до 200.")
        report = (
            inspection
            if inspection is not None
            else await self.inspect(
                section_id=section_id, document_id=document_id, verify=False
            )
        )
        # Полный аудит всех атомов выполняется административным dry-run/status.
        # Dispatcher за итерацию проверяет не более limit готовых поколений по кругу.
        ready = sorted(
            (row for row in report["documents"] if row["atomic_state"] == "ready"),
            key=lambda row: row["document_id"],
        )
        for index in range(min(limit, len(ready))):
            row = ready[(self.audit_cursor + index) % len(ready)]
            audited = await self.inspect(
                document_id=UUID(row["document_id"]), verify=True
            )
            row.update(audited["documents"][0])
        if ready:
            self.audit_cursor = (self.audit_cursor + min(limit, len(ready))) % len(
                ready
            )
        available = max(
            0,
            min(
                max_in_flight or self.catalog.policy.max_in_flight,
                self.catalog.policy.max_in_flight,
            )
            - report["in_flight"],
        )
        rows = sorted(
            report["documents"],
            key=lambda row: (not row["dispatch_pending"], row["document_id"]),
        )
        queued, outcomes = 0, []
        for row in rows:
            if (
                not row["eligible"]
                or (only_pending and not row["intent_exists"])
                or (not retry_failed and row["atomic_state"] == "failed")
            ):
                continue
            # Просроченная уже допущенная задача не требует дополнительного слота.
            admitted = (
                row["atomic_state"] in {"queued", "building"}
                and not row["dispatch_pending"]
            ) or row["superseded_in_flight"]
            if queued >= limit or (not admitted and available <= 0):
                continue
            try:
                result = await self.catalog.queue(
                    document_id=UUID(row["document_id"]),
                    respect_backoff=True,
                    max_in_flight=max_in_flight,
                )
            except (
                NormativeRequirementsConflictError,
                NormativeRequirementsNotFoundError,
            ) as error:
                outcomes.append(
                    {
                        "document_id": row["document_id"],
                        "state": "conflict",
                        "reason": str(error),
                    }
                )
                continue
            outcomes.append(
                {
                    "document_id": row["document_id"],
                    "state": result["state"],
                    "generation_id": result["generation_id"],
                    "dispatch_pending": result.get("dispatch_pending", False),
                }
            )
            queued += 1
            if not admitted:
                available -= 1
        return {**report, "selected": queued, "outcomes": outcomes}
