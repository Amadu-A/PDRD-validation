# services/knowledge-service/src/pdrd_knowledge_service/domain/technical_assignment_indexing.py

"""Domain-модели multimodal индексации ТЗ."""

import re
from dataclasses import dataclass

_NORMATIVE_REFERENCE_PATTERN = re.compile(
    r"\b(?:"
    r"ГОСТ(?:\s+Р)?"
    r"|GOST(?:\s+R)?"
    r"|СП"
    r"|SP"
    r"|СНиП"
    r"|SNIP"
    r"|ВСН"
    r"|VSN"
    r"|РД"
    r"|RD"
    r"|СТО"
    r"|STO"
    r")"
    r"\s+"
    r"[0-9A-Za-zА-Яа-яЁё]"
    r"[0-9A-Za-zА-Яа-яЁё.\-–—/:]*"
    r"|\b(?:ПУЭ|PUE)(?:\s+\d+)?\b",
    flags=re.IGNORECASE,
)

_REFERENCE_ALIAS_PATTERNS = (
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:ГОСТ\s*Р|GOST\s*R)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "GOSTR",
    ),
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:ГОСТ|GOST)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "GOST",
    ),
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:СП|SP)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "SP",
    ),
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:СНИП|SNIP)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "SNIP",
    ),
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:ВСН|VSN)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "VSN",
    ),
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:РД|RD)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "RD",
    ),
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:СТО|STO)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "STO",
    ),
    (
        re.compile(
            r"(?<![A-ZА-ЯЁ])"
            r"(?:ПУЭ|PUE)"
            r"(?![A-ZА-ЯЁ])",
        ),
        "PUE",
    ),
)


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentPage:
    """Одна физическая страница ТЗ."""

    page_number: int

    text: str

    image_bytes: bytes

    pixel_width: int

    pixel_height: int


def canonicalize_normative_reference(
    value: str,
) -> str:
    """Строит language-independent key нормативной ссылки."""
    normalized = (
        value.upper()
        .replace(
            "Ё",
            "Е",
        )
        .replace(
            "_",
            " ",
        )
        .replace(
            "–",
            "-",
        )
        .replace(
            "—",
            "-",
        )
    )

    for pattern, replacement in _REFERENCE_ALIAS_PATTERNS:
        normalized = pattern.sub(
            replacement,
            normalized,
        )

    return "".join(character for character in normalized if character.isalnum())


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

        key = canonicalize_normative_reference(
            value,
        )

        if not value or not key or key in seen:
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
