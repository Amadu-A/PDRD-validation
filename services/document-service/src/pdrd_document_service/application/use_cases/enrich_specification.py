# services/document-service/src/pdrd_document_service/application/use_cases/enrich_specification.py

"""Получение состава спецификации через общий сервис с независимой привязкой геометрии."""

import hashlib
import json
import logging
from dataclasses import dataclass, replace

from pdrd_document_service.application.ports.specification_extraction import (
    ExtractionArtifactStore,
    SpecificationExtractionError,
    SpecificationExtractor,
)
from pdrd_document_service.domain.external_specification import (
    bind_external_specification,
)
from pdrd_document_service.domain.pdf import PdfDocument, PdfPageType
from pdrd_document_service.domain.specification import project_code_from_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnrichSpecification:
    """Использует единственный источник состава и сохраняет явную причину резервного пути."""

    extractor: SpecificationExtractor
    allow_native_fallback: bool = True
    artifacts: ExtractionArtifactStore | None = None

    async def execute(
        self,
        *,
        document: PdfDocument,
        content: bytes,
        file_name: str,
        project_code: str = "",
    ) -> PdfDocument:
        """Выбирает только страницы с признаками спецификации, без повторного чтения всего PDF."""
        pages = tuple(
            p.number
            for p in document.pages
            if p.page_type == PdfPageType.SPECIFICATION
            or (p.equipment_specification or {}).get("raw_row_count", 0) > 0
        )
        if not pages:
            return document
        codes = {
            str((p.equipment_specification or {}).get("project_code", ""))
            for p in document.pages
        } - {""}
        if not project_code and len(codes) == 1:
            project_code = codes.pop()
        if not project_code:
            project_code = project_code_from_text(
                "\n".join(p.text for p in document.pages if p.number in pages)
            )
        try:
            result = await self.extractor.extract(
                content=content,
                file_name=file_name,
                pages=pages,
                project_code=project_code,
            )
        except (SpecificationExtractionError, TimeoutError) as error:
            if not self.allow_native_fallback:
                raise
            logger.warning(
                "specification_extract operation=external status=fallback error_type=%s pages=%s",
                type(error).__name__,
                pages,
            )
            return replace(
                document,
                pages=tuple(
                    replace(
                        p,
                        equipment_specification={
                            **(
                                p.equipment_specification
                                or {
                                    "version": 1,
                                    "page": p.number,
                                    "status": "unreliable",
                                    "coverage_complete": False,
                                    "raw_rows": [],
                                    "raw_row_count": 0,
                                    "items": [],
                                }
                            ),
                            "external_extraction": {
                                "status": "fallback",
                                "reason": type(error).__name__,
                            },
                        },
                    )
                    if p.number in pages
                    else p
                    for p in document.pages
                ),
            )
        enriched = []
        for page in document.pages:
            if page.number not in pages:
                enriched.append(page)
                continue
            native = {
                **(page.equipment_specification or {}),
                "native_spans": [
                    {
                        "text": word.text,
                        "x_min": word.bbox.x_min,
                        "y_min": word.bbox.y_min,
                        "x_max": word.bbox.x_max,
                        "y_max": word.bbox.y_max,
                    }
                    for word in page.text_words
                ],
                "graphic_lines": list(page.graphic_lines),
            }
            specification = bind_external_specification(
                page_number=page.number,
                native=native,
                extraction=result.result,
                job_id=result.job_id,
                artifact_key=result.artifact_key,
                warnings=result.warnings,
            )
            if self.artifacts is not None:
                value = {
                    "physical_page": page.number,
                    "native_geometry": native,
                    "external_artifact_key": result.artifact_key,
                }
                key = hashlib.sha256(
                    json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                await self.artifacts.save(key, value)
                specification["native_geometry_artifact_key"] = key
            enriched.append(replace(page, equipment_specification=specification))
        return replace(document, pages=tuple(enriched))
