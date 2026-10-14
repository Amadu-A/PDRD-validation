# services/api-gateway/src/pdrd_api_gateway/application/use_cases/equipment_update.py

"""Строит EQ-дополнение из сохранённого отчёта, не подменяя результат анализа."""

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from pdrd_api_gateway.application.ports.equipment_updates import (
    EquipmentComparison,
    EquipmentJobReader,
)
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.get_analysis_result import GetAnalysisResult
from pdrd_api_gateway.core.observability import log_execution_time


def inventory_pages(inventory: list[dict]) -> list[dict]:
    """Восстанавливает только исходные факты и физические вхождения оборудования."""
    pages = defaultdict(list)
    for item in inventory:
        identity = {key: value for key, value in item.items() if key != "occurrences"}
        for occurrence in item.get("occurrences", []):
            page = int(occurrence.get("page") or 0)
            if page > 0:
                pages[page].append({**identity, **occurrence})
    return [
        {"page_number": page, "facts": {"equipment_identities": rows}}
        for page, rows in sorted(pages.items())
    ]


@dataclass(frozen=True, slots=True)
class BuildEquipmentUpdate:
    """Отдельное версионируемое предложение; принятые замечания не изменяются."""

    results: GetAnalysisResult
    jobs: EquipmentJobReader
    comparison: EquipmentComparison

    @log_execution_time(operation="equipment_report_update")
    async def execute(self, *, job_id: UUID, revision: int | None = None) -> dict:
        """Читает факты из сервера; браузер передаёт только идентификатор версии."""
        original = await self.results.execute(job_id=job_id)
        check = original.get("equipment_check") or {}
        if not check.get("enabled"):
            raise ReviewRequestError(409, "EQ не был включён для этого анализа.")
        job = await self.results.get_analysis_job.execute(job_id=job_id)
        search = (
            await self.jobs.version(job.document_id, revision)
            if revision is not None
            else await self.jobs.state(job.document_id)
        )
        report = await self.comparison.compare(
            inventory_pages(check.get("inventory") or []), search
        )
        return {
            "job_id": str(job_id),
            "revision": int(search.get("revision") or 0),
            "search": search,
            "report": report,
            "comparison_version": "equipment-comparison-v2",
            "findings": [
                candidate
                for rows in report.get("candidates_by_page", {}).values()
                for candidate in rows
            ],
        }
