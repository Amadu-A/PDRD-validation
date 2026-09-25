# services/experience-service/src/pdrd_experience_service/infrastructure/analysis/visualization.py

"""Переносит серверный результат анализа и визуализации в Review.

Входные данные должны быть прочитаны доверенным backend-адаптером,
а не получены из тела браузерного запроса. Координаты здесь являются
предложением локализатора и не подтверждают пригодность для Experience.
"""

import hashlib
import math
from typing import Any
from uuid import UUID

from pdrd_experience_service.application.ports.review import CompletedAnalysis
from pdrd_experience_service.domain.review import (
    OriginalFinding,
    ProposedRegion,
    Rectangle,
    ReviewError,
)


def _page(value: Any) -> int:
    """Принимает только физический положительный номер страницы."""
    if isinstance(value, bool):
        raise ReviewError("Номер листа не может быть логическим значением.")

    if isinstance(value, int):
        page = value
    elif isinstance(value, str) and value.isdecimal():
        page = int(value)
    else:
        raise ReviewError("У замечания отсутствует номер физического листа.")

    if page < 1:
        raise ReviewError("Номер физического листа должен быть положительным.")

    return page


def _number(value: Any) -> float:
    """Отклоняет строки, NaN и булевы значения в геометрии."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReviewError("Координата должна быть конечным числом.")

    result = float(value)

    if not math.isfinite(result):
        raise ReviewError("Координата должна быть конечным числом.")

    return result


def _regions(location: dict[str, Any]) -> tuple[ProposedRegion, ...]:
    """Читает только валидные области located finding без синтеза рамок."""
    if location.get("status") != "located":
        return ()

    method = location.get("method")
    raw_regions = location.get("regions")

    if not isinstance(method, str) or not isinstance(raw_regions, list):
        return ()

    regions: list[ProposedRegion] = []

    for raw in raw_regions[:4]:
        if not isinstance(raw, dict) or not isinstance(raw.get("bbox"), dict):
            continue

        box = raw["bbox"]

        try:
            regions.append(
                ProposedRegion(
                    bbox=Rectangle(
                        x_min=_number(box.get("x_min")),
                        y_min=_number(box.get("y_min")),
                        x_max=_number(box.get("x_max")),
                        y_max=_number(box.get("y_max")),
                    ),
                    source=raw.get("source"),
                    confidence=_number(raw.get("confidence")),
                    method=method,
                )
            )
        except ReviewError:
            continue

    return tuple(regions)


def completed_analysis_from_visualization(
    *,
    job_id: UUID,
    document_id: UUID,
    source_filename: str,
    pdf_content: bytes,
    result: dict[str, Any],
    visualization: dict[str, Any],
) -> CompletedAnalysis:
    """Строит исходные находки с непроверенными предложениями областей."""
    if (
        not isinstance(pdf_content, bytes)
        or not isinstance(result, dict)
        or not isinstance(visualization, dict)
        or visualization.get("job_id") != str(job_id)
        or visualization.get("document_id") != str(document_id)
    ):
        raise ReviewError("Визуализация не относится к исходному заданию.")

    pages = visualization.get("pages")
    raw_findings = result.get("findings")

    if not isinstance(pages, list) or not isinstance(raw_findings, list):
        raise ReviewError("Завершённый анализ не содержит страницы или замечания.")

    rendered_pages: list[int] = []
    locations: dict[tuple[int, str], tuple[ProposedRegion, ...]] = {}
    ambiguous: set[tuple[int, str]] = set()

    for page_payload in pages:
        if not isinstance(page_payload, dict):
            raise ReviewError("Визуализация содержит неверный лист.")

        page_number = _page(page_payload.get("page_number"))

        if page_number in rendered_pages:
            raise ReviewError("Лист визуализации повторяется.")

        rendered_pages.append(page_number)
        raw_locations = page_payload.get("locations", [])

        if not isinstance(raw_locations, list):
            raise ReviewError("Лист содержит неверный список областей.")

        for location in raw_locations:
            if not isinstance(location, dict):
                continue

            finding_id = location.get("finding_id")

            if not isinstance(finding_id, str) or not finding_id.strip():
                continue

            key = (page_number, finding_id)

            if key in locations:
                ambiguous.add(key)

            locations[key] = _regions(location)

    originals: list[OriginalFinding] = []
    seen_ids: set[str] = set()

    for finding in raw_findings:
        if not isinstance(finding, dict):
            raise ReviewError("Результат анализа содержит неверное замечание.")

        if finding.get("status") == "hypothesis":
            continue

        finding_id = finding.get("finding_id")

        if not isinstance(finding_id, str) or finding_id in seen_ids:
            raise ReviewError("ID исходного замечания отсутствует или повторяется.")

        seen_ids.add(finding_id)
        page_number = _page(finding.get("page", finding.get("page_number")))
        key = (page_number, finding_id)
        normative_basis = finding.get("normative_basis", "")

        if not isinstance(normative_basis, str):
            raise ReviewError("Нормативное основание имеет неверный формат.")

        originals.append(
            OriginalFinding(
                finding_id=finding_id,
                page_number=page_number,
                text=finding.get("comment"),
                normative_basis=normative_basis,
                proposed_regions=(() if key in ambiguous else locations.get(key, ())),
            )
        )

    return CompletedAnalysis(
        job_id=job_id,
        document_id=document_id,
        source_filename=source_filename,
        source_sha256=hashlib.sha256(pdf_content).hexdigest(),
        findings=tuple(originals),
        rendered_pages=tuple(rendered_pages),
    )
