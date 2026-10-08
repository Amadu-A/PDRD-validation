# services/knowledge-service/src/pdrd_knowledge_service/application/equipment_facts.py

"""Консервативное текстовое извлечение характеристик точной модели."""

import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any

from pdrd_knowledge_service.application.equipment_discrete_values import (
    parse_discrete_value,
)

SCHEMA_VERSION = 1
EXTRACTOR_VERSION = 7

_PROPERTY_LABELS = {
    "operating_mode": ("operating mode", "режим работы"),
    "contact_configuration": ("contact configuration", "конфигурация контактов"),
    "trip_curve": (
        "tripping characteristic",
        "trip curve",
        "характеристика расцепления",
    ),
    "protection_degree": ("protection degree", "степень защиты"),
    "requires_earthing": ("requires earthing", "требуется заземление"),
    "input_voltage": ("input voltage", "входное напряжение", "напряжение входа"),
    "output_voltage": ("output voltage", "выходное напряжение", "напряжение выхода"),
    "rated_voltage": ("rated voltage", "номинальное напряжение"),
    "input_current": ("input current", "входной ток"),
    "output_current": ("output current", "выходной ток"),
    "rated_current": ("rated current", "номинальный ток"),
    "power": ("power", "мощность"),
    "frequency": ("frequency", "частота"),
    "ambient_temperature": ("ambient temperature", "температура окружающей среды"),
}
_UNIT = r"(?:mV|kV|V|mA|A|kW|W|kHz|Hz|°C|мВ|кВ|В|мА|А|кВт|Вт|кГц|Гц|°С)"
_NUMBER = r"[+-]?\d+(?:[.,]\d+)?"
_VALUE = re.compile(
    rf"(?P<value>{_NUMBER}(?:\s*(?:\.{{2,}}|–|—|-|to)\s*{_NUMBER})?)"
    rf"\s*(?P<unit>{_UNIT})(?![\w-])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ExtractedEquipmentFact:
    """Факт с точной строкой, страницей и применимостью модели."""

    fact_id: str
    property_type: str
    property_name: str
    value_raw: str
    unit_raw: str
    normalized_value: dict[str, Any]
    value_type: str
    role: str
    current_type: str
    phase: str
    mode: str
    condition: str
    variant: str
    configuration: str
    document_source_id: str
    page: int
    table_title: str
    row_label: str
    column_label: str
    snippet: str
    confidence: float
    applicability: str


def _phase(line: str) -> str:
    """Определяет фазность только по явной маркировке в строке модели."""
    if re.search(r"\b(?:single|1)[-\s]?phase\b|однофаз", line, re.I):
        return "single"
    if re.search(r"\b(?:three|3)[-\s]?phase\b|тр[её]хфаз", line, re.I):
        return "three"
    return ""


def _conditional(line: str) -> bool:
    """Не подтверждает расхождение по значению с особыми условиями."""
    return (
        re.search(
            r"[<>≤≥±]|\b(?:at|under|when|not|typ(?:ical)?|max(?:imum)?|min(?:imum)?|no[-\s]?load|full[-\s]?load)\b|"
            r"не\s+(?:менее|более)|\bпри\s+[+-]?\d|без\s+нагрузки|при\s+нагрузке",
            line,
            re.I,
        )
        is not None
    )


def _contains_exact(line: str, marker: str) -> bool:
    """Ищет модель как отдельную маркировку, не часть другого артикула."""
    return (
        re.search(
            rf"(?<![\w-]){re.escape(marker.casefold())}(?![\w-])",
            line.casefold(),
        )
        is not None
    )


def _ambiguous_model_line(line: str, model: str) -> bool:
    """Не присваивает общую строку каталога одному из соседних обозначений."""
    prefix = re.match(r"[A-Za-zА-Яа-я]+", model)
    if prefix is None:
        return True
    markers = re.findall(rf"(?<![\w]){re.escape(prefix.group())}[\w.-]*", line, re.I)
    return any(marker.rstrip(".").casefold() != model.casefold() for marker in markers)


def _single_model_heading(line: str, model: str, variant: str) -> bool:
    """Принимает только заголовок одной модели, не общий каталог исполнений."""
    marker = re.escape(model.strip())
    if variant:
        marker += r"\s+" + re.escape(variant.strip())
    return (
        re.fullmatch(
            rf"(?:model|модель)\s*[:№#-]?\s*{marker}"
            rf"|(?:parameter|параметр)\s*\|\s*{marker}",
            line.strip(),
            re.IGNORECASE,
        )
        is not None
    )


def _scoped_lines(
    text: str,
    model: str,
    variant: str,
) -> list[tuple[str, str, str]]:
    """Привязывает соседние строки таблицы только к явному заголовку одной модели."""
    result: list[tuple[str, str, str]] = []
    heading = ""
    remaining = 0
    column_index = -1
    column_count = 0
    target = " ".join(part for part in (model.strip(), variant.strip()) if part)
    for raw in text.splitlines():
        line = " ".join(raw.split()).strip(" |")[:500]
        if not line:
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) >= 3 and cells[0].casefold() in {
            "parameter",
            "параметр",
            "model",
            "модель",
        }:
            matches = [
                index
                for index, cell in enumerate(cells[1:], start=1)
                if cell.casefold() == target.casefold()
            ]
            heading = line if len(matches) == 1 else ""
            column_index = matches[0] if len(matches) == 1 else -1
            column_count = len(cells)
            remaining = 12 if heading else 0
            continue
        if _single_model_heading(line, model, variant):
            heading = line
            remaining = 12
            column_index = -1
            continue
        if re.match(
            r"^(?:(?:model|модель)\s*[:№#-]|(?:parameter|параметр)\s*\|)",
            line,
            re.I,
        ) or (remaining and re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{3,}", line)):
            heading = ""
            remaining = 0
            column_index = -1
        if (
            remaining
            and column_index > 0
            and len(cells) == column_count
            and any(
                label in cells[0].casefold()
                for labels in _PROPERTY_LABELS.values()
                for label in labels
            )
        ):
            scoped = f"{target} | {cells[0]} | {cells[column_index]}"
            result.append((scoped, "exact_model_column", heading))
        elif _contains_exact(line, model) and (
            not variant or _contains_exact(line, variant)
        ):
            applicability = (
                "ambiguous_model_line"
                if _ambiguous_model_line(line, model)
                else "exact_model_line"
            )
            result.append((line, applicability, ""))
        elif (
            remaining
            and heading
            and column_index < 0
            and any(
                label in line.casefold()
                for labels in _PROPERTY_LABELS.values()
                for label in labels
            )
        ):
            result.append((line, "single_model_block", heading))
        if remaining:
            remaining -= 1
    return result


def extract_equipment_facts(
    *,
    source_id: str,
    model: str,
    variant: str,
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Принимает лишь строки, где модель и характеристика подтверждены вместе."""
    if not source_id or not model:
        return []
    extracted: list[dict[str, Any]] = []
    for page in pages[:40]:
        number = int(page.get("page", 0))
        text = str(page.get("text") or "")[:20_000]
        for line, applicability, heading in _scoped_lines(text, model, variant):
            for property_type, labels in _PROPERTY_LABELS.items():
                label_match = next(
                    (
                        found
                        for label in sorted(labels, key=len, reverse=True)
                        if (found := re.search(re.escape(label), line, re.I))
                        is not None
                    ),
                    None,
                )
                if label_match is None:
                    continue
                discrete = parse_discrete_value(
                    property_type, line[label_match.end() :]
                )
                if discrete is not None:
                    value_type, normalized, value = discrete
                    unit = ""
                else:
                    matches = list(_VALUE.finditer(line[label_match.end() :]))
                    if len(matches) != 1:
                        continue
                    match = matches[0]
                    value = match.group("value")
                    unit = match.group("unit")
                    normalized_range = re.sub(
                        r"(?<=\d)-(?=\d)",
                        "...",
                        value,
                    )
                    numbers = [
                        Decimal(part.replace(",", "."))
                        for part in re.findall(_NUMBER, normalized_range)
                    ]
                    if not numbers or len(numbers) > 2:
                        continue
                    value_type = "range" if len(numbers) == 2 else "number"
                    normalized = (
                        {
                            "min": str(min(numbers)),
                            "max": str(max(numbers)),
                            "unit": unit,
                        }
                        if value_type == "range"
                        else {"value": str(numbers[0]), "unit": unit}
                    )
                current_type = (
                    "AC/DC"
                    if re.search(r"\bAC/DC\b", line, re.I)
                    else "DC"
                    if re.search(r"\bDC\b", line, re.I)
                    else "AC"
                    if re.search(r"\bAC\b", line, re.I)
                    else ""
                )
                role = (
                    "input"
                    if property_type.startswith("input_")
                    else "output"
                    if property_type.startswith("output_")
                    else ""
                )
                stable = "|".join((source_id, str(number), property_type, value, line))
                fact = ExtractedEquipmentFact(
                    fact_id="EQF-" + sha256(stable.encode()).hexdigest()[:20],
                    property_type=property_type,
                    property_name=line[label_match.start() : label_match.end()],
                    value_raw=value,
                    unit_raw=unit,
                    normalized_value=normalized,
                    value_type=value_type,
                    role=role,
                    current_type=current_type,
                    phase=_phase(line),
                    mode="",
                    condition=line if _conditional(line) else "",
                    variant=variant,
                    configuration="",
                    document_source_id=source_id,
                    page=number,
                    table_title=heading,
                    row_label=line,
                    column_label=model if heading else "",
                    snippet=line,
                    confidence=(
                        0.7
                        if _conditional(line) or applicability == "ambiguous_model_line"
                        else 0.9
                        if applicability in {"exact_model_line", "exact_model_column"}
                        else 0.8
                    ),
                    applicability=applicability,
                )
                extracted.append(asdict(fact))
    return extracted[:100]
