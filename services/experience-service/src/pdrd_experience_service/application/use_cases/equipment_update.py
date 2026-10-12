# services/experience-service/src/pdrd_experience_service/application/use_cases/equipment_update.py

"""Явно добавляет серверное EQ-дополнение через атомарное хранилище Review."""

from datetime import UTC, datetime
from uuid import UUID

from pdrd_experience_service.application.ports.review import ReviewRepository
from pdrd_experience_service.core.observability import log_execution_time
from pdrd_experience_service.domain.review import (
    OriginalFinding,
    ProposedRegion,
    Rectangle,
    ReviewError,
    ReviewSession,
)


def original_equipment_finding(raw: dict, version: int) -> OriginalFinding:
    """Принимает только EQ-кандидат с сохранённым доказательством и идентичностью."""
    sources = tuple(raw.get("equipment_documentation_basis_sources") or ())
    if (
        not str(raw.get("finding_id") or "").startswith("EQ-")
        or not sources
        or raw.get("status") not in {"confirmed", "needs_review"}
        or any(
            not source.get("source_id")
            or not source.get("sha256")
            or not source.get("snippet")
            or int(source.get("page") or 0) < 1
            for source in sources
        )
    ):
        raise ReviewError(
            "EQ-находка не содержит подтверждённого snapshot и доказательства."
        )
    regions = []
    for raw_region in raw.get("visual_regions") or []:
        bbox = raw_region.get("bbox")
        if isinstance(bbox, dict):
            regions.append(
                ProposedRegion(
                    Rectangle(**bbox),
                    str(raw_region.get("source") or "eq_project_evidence"),
                    float(raw_region.get("confidence") or 0),
                    str(raw_region.get("method") or "page_understanding"),
                )
            )
    return OriginalFinding(
        finding_id=raw["finding_id"],
        page_number=int(raw["page"]),
        text=str(raw["comment"]),
        normative_basis="Техническая документация производителя (EQ)",
        proposed_regions=tuple(regions),
        equipment_documentation_basis_sources=sources,
        equipment_details={
            **(raw.get("equipment_details") or {}),
            "equipment_report_revision": version,
        },
        source_kinds=("EQ",),
    )


@log_execution_time(operation="review_equipment_update")
async def import_equipment_update(
    repository: ReviewRepository,
    *,
    job_id: UUID,
    actor: str,
    expected_revision: int,
    equipment_revision: int,
    findings: list[dict],
) -> ReviewSession:
    """Добавляет новые pending-находки; существующие решения и оригиналы сохраняются."""
    current = await repository.load(job_id)
    if current is None:
        raise LookupError("Human Review ещё не открыт.")
    originals = tuple(
        original_equipment_finding(raw, equipment_revision) for raw in findings
    )
    updated = current.add_equipment_update(
        originals=originals,
        equipment_revision=equipment_revision,
        actor=actor,
        at=datetime.now(UTC),
        expected_revision=expected_revision,
    )
    if updated is not current:
        await repository.update(updated, expected_revision=expected_revision)
    return updated
