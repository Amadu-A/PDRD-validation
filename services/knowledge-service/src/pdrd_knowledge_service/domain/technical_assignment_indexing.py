# services/knowledge-service/src/pdrd_knowledge_service/domain/technical_assignment_indexing.py

"""Domain-модели multimodal индексации ТЗ."""

import re
from dataclasses import dataclass

_NORMATIVE_REFERENCE_PATTERN = re.compile(
    r"\b(?:"
    r"ГОСТ(?:\s+Р)?"
    r"|СП"
    r"|СНиП"
    r"|ВСН"
    r"|РД"
    r"|СТО"
    r")"
    r"\s+"
    r"[0-9A-Za-zА-Яа-яЁё]"
    r"[0-9A-Za-zА-Яа-яЁё.\-–—/:]*"
    r"|\bПУЭ\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentPage:
    """Одна физическая страница ТЗ."""

    page_number: int

    text: str

    image_bytes: bytes

    pixel_width: int

    pixel_height: int


def extract_normative_references(
    text: str,
) -> tuple[
    str,
    ...,
]:
    """Извлекает явные ссылки на нормативные документы."""
    result: list[str] = []

    seen: set[str] = set()

    for match in _NORMATIVE_REFERENCE_PATTERN.finditer(
        text,
    ):
        value = " ".join(
            match.group(
                0,
            ).split()
        ).strip(".,;:()[]")

        key = value.casefold()

        if not value or key in seen:
            continue

        seen.add(
            key,
        )

        result.append(
            value,
        )

    return tuple(
        result,
    )
