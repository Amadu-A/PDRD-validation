# services/experience-service/src/pdrd_experience_service/application/use_cases/export_catalog.py

"""Версионированный ZIP каталога с полными текстами, происхождением и crop.

Экспорт — переносимая копия опыта, а не автоматический датасет fine-tuning.
needs_adjudication и неактуальность источника остаются явными в manifest.
"""

import csv
import io
import json
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from pdrd_experience_service.application.catalog_snapshot import example_to_json
from pdrd_experience_service.application.ports.catalog import (
    CatalogRepository,
    CropStore,
)
from pdrd_experience_service.core.observability import log_execution_time
from pdrd_experience_service.domain.catalog import CatalogFilter
from pdrd_experience_service.domain.index_projection import index_projection
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError


@dataclass(frozen=True, slots=True)
class ExportCatalog:
    """Постраничное чтение и ограничение объёма исключают бесконтрольную выгрузку."""

    repository: CatalogRepository
    crops: CropStore
    max_examples: int = 1000
    max_bytes: int = 100_000_000

    async def _entries(self, criteria: CatalogFilter):
        """Фиксирует весь отфильтрованный набор, независимо от страницы UI."""
        entries, offset = [], 0
        while True:
            page, total = await self.repository.list(
                replace(criteria, offset=offset, limit=100)
            )
            if total > self.max_examples:
                raise ReviewError("Для выгрузки более 1000 примеров уточните фильтры.")
            entries.extend(page)
            offset += len(page)
            if offset >= total or not page:
                return tuple(entries)

    @log_execution_time(operation="experience_export")
    async def execute(self, criteria: CatalogFilter) -> bytes:
        """Изменившийся набор или crop отвергается; формулы CSV экранируются."""
        entries = await self._entries(criteria)
        buffer = io.BytesIO()
        rows, size = [], 0
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in entries:
                example = entry.example
                row = example_to_json(example)
                row.update(
                    tag=example.tag,
                    decision=example.source.decision.value,
                    reason_category=example.source.reason_category,
                    comment=example.source.comment,
                    learning_use=example.learning_use,
                    active=entry.active,
                    requested_active=example.active,
                    source_current=entry.source_current,
                    training_eligible=index_projection(entry) is not None,
                )
                paths = []
                for index, crop in enumerate(example.crops):
                    image = await self.crops.read(crop)
                    size += len(image)
                    if size > self.max_bytes:
                        raise ReviewError(
                            "Выгрузка превышает 100 МБ; уточните фильтры."
                        )
                    path = f"crops/{example.id}/{index}.png"
                    archive.writestr(path, image)
                    paths.append(path)
                row["crop_files"] = paths
                rows.append(row)
            manifest = {
                "format": "pdrd-experience",
                "version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "examples": rows,
            }
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            table = io.StringIO(newline="")
            writer = csv.writer(table)
            writer.writerow(
                [
                    "id",
                    "document",
                    "page",
                    "text",
                    "normative_basis",
                    "tag",
                    "decision",
                    "reason_category",
                    "comment",
                    "learning_use",
                    "active",
                    "source_current",
                    "source_sha256",
                    "section_id",
                    "section_title",
                    "author",
                    "created_at",
                ]
            )
            for entry in entries:
                example = entry.example
                values = [
                    str(example.id),
                    example.document_title,
                    example.source.page_number,
                    example.text,
                    example.normative_basis,
                    example.tag,
                    example.source.decision.value,
                    example.source.reason_category or "",
                    example.source.comment,
                    example.learning_use,
                    entry.active,
                    entry.source_current,
                    example.source.source_sha256,
                    example.section_id,
                    example.section_title,
                    example.source.created_by,
                    example.created_at.isoformat(),
                ]
                writer.writerow(
                    [
                        "'" + value
                        if isinstance(value, str)
                        and value.lstrip().startswith(("=", "+", "-", "@"))
                        else value
                        for value in values
                    ]
                )
            archive.writestr("examples.csv", table.getvalue().encode("utf-8-sig"))
        if await self._entries(criteria) != entries:
            raise ReviewConflictError(
                "Каталог изменён во время выгрузки; повторите операцию."
            )
        return buffer.getvalue()
