# services/knowledge-service/src/pdrd_knowledge_service/domain/technical_assignment_indexing.py

"""Domain-модели multimodal индексации ТЗ."""

import re
from dataclasses import dataclass
from typing import Literal

RequirementStrength = Literal[
    "explicit",
    "candidate",
]

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

_REQUIREMENT_EXPLICIT_PATTERN = re.compile(
    r"\b(?:"
    r"предусмотр(?:еть|ен|ена|ено|ены)"
    r"|выполн(?:ить|яется|ить)"
    r"|обеспеч(?:ить|ивается)"
    r"|принять"
    r"|разработать"
    r"|установить"
    r"|оснастить"
    r"|применить"
    r"|учесть"
    r"|согласовать"
    r"|исключить"
    r"|сохранить"
    r"|определить"
    r"|указать"
    r"|предоставить"
    r"|долж(?:ен|на|но|ны)"
    r"|необходимо"
    r"|требуется"
    r"|следует"
    r"|запрещается"
    r"|не\s+допускается"
    r")\b",
    flags=re.IGNORECASE,
)

_REQUIREMENT_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[.!?;])"
    r"\s+"
    r"(?=(?:"
    r"[0-9A-ZА-ЯЁ«\"(]"
    r"|[-–—•]"
    r"))"
)

_REQUIREMENT_NUMBERED_LINE_BOUNDARY = re.compile(
    r"\n"
    r"(?=\s*(?:"
    r"\d+(?:\.\d+){1,4}"
    r"|\d+\)"
    r"|[-–—•]"
    r")\s+)"
)

_REQUIREMENT_LETTER_PATTERN = re.compile(
    r"[A-Za-zА-Яа-яЁё]",
)

_REQUIREMENT_MIN_CHARACTERS = 20

_REQUIREMENT_MIN_LETTERS = 8

_REQUIREMENT_MAX_CHARACTERS = 1200

_SCOPE_PATTERNS = (
    (
        "electrical",
        re.compile(
            r"(?:"
            r"электроснаб"
            r"|электроустанов"
            r"|электрооборуд"
            r"|кабел"
            r"|авр"
            r"|зазем"
            r"|молниезащит"
            r"|электрощит"
            r"|распределительн(?:ый|ого)\s+щит"
            r"|напряжени"
            r"|0[,.]4\s*кв"
            r"|6\s*кв"
            r"|10\s*кв"
            r")",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "automation",
        re.compile(
            r"(?:"
            r"автоматизац"
            r"|кип"
            r"|контроллер"
            r"|датчик"
            r"|диспетчеризац"
            r"|асу"
            r"|сигнализац"
            r"|управлени"
            r")",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "thermal",
        re.compile(
            r"(?:"
            r"котельн"
            r"|теплоснаб"
            r"|теплонос"
            r"|отоплен"
            r"|вентиляц"
            r"|гвс"
            r"|газоснаб"
            r"|трубопровод"
            r")",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "fire_safety",
        re.compile(
            r"(?:"
            r"пожар"
            r"|огнестой"
            r"|дымоудален"
            r"|противопожар"
            r"|спз"
            r")",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "civil",
        re.compile(
            r"(?:"
            r"архитект"
            r"|конструкц"
            r"|фундамент"
            r"|здани"
            r"|сооружени"
            r"|кровл"
            r")",
            flags=re.IGNORECASE,
        ),
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


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentRequirement:
    """Один high-recall atomic candidate из ТЗ."""

    page_number: int

    ordinal: int

    text: str

    source_text: str

    scopes: tuple[
        str,
        ...,
    ]

    normative_refs: tuple[
        str,
        ...,
    ]

    strength: RequirementStrength

    @property
    def local_key(
        self,
    ) -> str:
        """Возвращает deterministic key внутри T-document."""
        return f"p{self.page_number}-r{self.ordinal}"


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


def infer_requirement_scopes(
    text: str,
) -> tuple[
    str,
    ...,
]:
    """Строит non-destructive scope hints требования."""
    scopes = tuple(
        scope
        for scope, pattern in _SCOPE_PATTERNS
        if pattern.search(
            text,
        )
    )

    if scopes:
        return scopes

    return ("general",)


def extract_technical_assignment_requirements(
    *,
    page_number: int,
    text: str,
) -> tuple[
    TechnicalAssignmentRequirement,
    ...,
]:
    """Атомизирует recovered T text без semantic suppression."""
    fragments = _requirement_fragments(
        text,
    )

    requirements: list[TechnicalAssignmentRequirement] = []

    for fragment in fragments:
        normalized = _normalize_requirement_fragment(
            fragment,
        )

        if not _is_meaningful_requirement_fragment(
            normalized,
        ):
            continue

        requirements.append(
            TechnicalAssignmentRequirement(
                page_number=page_number,
                ordinal=(
                    len(
                        requirements,
                    )
                    + 1
                ),
                text=normalized,
                source_text=fragment.strip(),
                scopes=infer_requirement_scopes(
                    normalized,
                ),
                normative_refs=(
                    extract_normative_references(
                        normalized,
                    )
                ),
                strength=(
                    "explicit"
                    if _REQUIREMENT_EXPLICIT_PATTERN.search(
                        normalized,
                    )
                    else "candidate"
                ),
            )
        )

    return tuple(
        requirements,
    )


def _requirement_fragments(
    text: str,
) -> tuple[
    str,
    ...,
]:
    """Делит страницу на bounded source-order fragments."""
    prepared = (
        text.replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
        .replace(
            "\x00",
            " ",
        )
    )

    prepared = re.sub(
        (
            r"(?<=[A-Za-zА-Яа-яЁё])"
            r"-\n"
            r"(?=[a-zа-яё])"
        ),
        "",
        prepared,
    )

    prepared = _REQUIREMENT_NUMBERED_LINE_BOUNDARY.sub(
        "\n\n",
        prepared,
    )

    paragraphs = tuple(
        paragraph.strip()
        for paragraph in re.split(
            r"\n{2,}",
            prepared,
        )
        if paragraph.strip()
    )

    result: list[str] = []

    for paragraph in paragraphs:
        compact = _normalize_requirement_fragment(
            paragraph,
        )

        if not compact:
            continue

        sentences = tuple(
            sentence.strip()
            for sentence in _REQUIREMENT_SENTENCE_BOUNDARY.split(
                compact,
            )
            if sentence.strip()
        )

        for sentence in sentences:
            result.extend(
                _split_long_requirement_fragment(
                    sentence,
                )
            )

    return tuple(
        result,
    )


def _split_long_requirement_fragment(
    value: str,
) -> tuple[
    str,
    ...,
]:
    """Разбивает только чрезмерно длинный fragment."""
    normalized = _normalize_requirement_fragment(
        value,
    )

    if (
        len(
            normalized,
        )
        <= _REQUIREMENT_MAX_CHARACTERS
    ):
        return (normalized,)

    words = normalized.split()

    result: list[str] = []

    current: list[str] = []

    current_length = 0

    for word in words:
        additional_length = len(
            word,
        ) + (1 if current else 0)

        if current and (
            current_length + additional_length > _REQUIREMENT_MAX_CHARACTERS
        ):
            result.append(
                " ".join(
                    current,
                )
            )

            current = [
                word,
            ]

            current_length = len(
                word,
            )

            continue

        current.append(
            word,
        )

        current_length += additional_length

    if current:
        result.append(
            " ".join(
                current,
            )
        )

    return tuple(
        result,
    )


def _normalize_requirement_fragment(
    value: str,
) -> str:
    """Нормализует whitespace без semantic rewriting."""
    return " ".join(value.split()).strip()


def _is_meaningful_requirement_fragment(
    value: str,
) -> bool:
    """Отбрасывает только явно пустой/неинформативный OCR noise."""
    if (
        len(
            value,
        )
        < _REQUIREMENT_MIN_CHARACTERS
    ):
        return False

    letters = len(
        _REQUIREMENT_LETTER_PATTERN.findall(
            value,
        )
    )

    return letters >= _REQUIREMENT_MIN_LETTERS
