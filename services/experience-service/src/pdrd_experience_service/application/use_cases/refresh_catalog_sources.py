# services/experience-service/src/pdrd_experience_service/application/use_cases/refresh_catalog_sources.py

"""Восстановление старых метаданных перед ручной подготовкой выбранного набора."""

from dataclasses import dataclass

from pdrd_experience_service.application.use_cases.capture_experience import (
    CaptureExperience,
)
from pdrd_experience_service.domain.catalog import CatalogEntry


@dataclass(frozen=True, slots=True)
class RefreshCatalogSources:
    """Повторяет утверждённую материализацию с CAS, не запускает индексацию."""

    capture: CaptureExperience

    async def execute(
        self, *, entries: tuple[CatalogEntry, ...], actor: str
    ) -> tuple[dict, ...]:
        """Автоматически дополняет раздел и provenance только у актуальных источников."""
        jobs = {}
        for entry in entries:
            item, source = entry.example, entry.example.source
            if not entry.active:
                continue
            if (
                not item.section_id
                or not item.section_title
                or (source.origin.value == "vlm" and not source.proposed_regions)
            ):
                jobs.setdefault((source.job_id, source.approved_revision), []).append(
                    (item.id, item.revision)
                )
        repaired = []
        for (job_id, revision), references in jobs.items():
            result = await self.capture.execute(
                job_id=job_id,
                expected_revision=revision,
                actor=actor,
                expected_catalog_revisions=tuple(references),
            )
            repaired.extend(result["repaired"])
        return tuple(repaired)
