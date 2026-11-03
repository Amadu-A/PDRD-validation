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
EXTRACTOR_VERSION = 10

_PROPERTY_LABELS = {
    "outer_diameter": ("наружный диаметр", "outer diameter"),
    "nominal_diameter": ("номинальный диаметр", "условный диаметр", "nominal diameter"),
    "rated_pressure": ("номинальное давление", "rated pressure"),
    "working_pressure": ("рабочее давление", "working pressure"),
    "rated_flow_rate": ("номинальный расход", "rated flow rate"),
    "max_flow_rate": ("максимальный расход", "maximum flow rate"),
    "useful_volume": ("полезный объём", "полезный объем", "useful volume"),
    "diameter": ("наружный диаметр", "номинальный диаметр", "диаметр", "diameter"),
    "wall_thickness": ("толщина стенки", "wall thickness"),
    "pressure": ("рабочее давление", "номинальное давление", "pressure"),
    "flow_rate": ("номинальный расход", "расход воды", "flow rate"),
    "volume": ("объём", "объем", "volume"),
    "load_capacity": ("испытательная нагрузка", "нагрузка", "load capacity"),
    "sensor_type": ("sensor type", "тип датчика", "тип термопреобразователя"),
    "rtd_wires": ("rtd wires", "число проводов датчика"),
    "measurement_error": ("measurement error", "погрешность измерения"),
    "accessory_model": (
        "compatible accessory",
        "совместимая колодка",
        "совместимые колодки",
    ),
    "mounting_width": ("mounting width", "монтажная ширина"),
    "mounting_height": ("mounting height", "монтажная высота"),
    "mounting_depth": ("mounting depth", "монтажная глубина"),
    "operating_mode": ("operating mode", "режим работы"),
    "contact_configuration": ("contact configuration", "конфигурация контактов"),
    "trip_curve": (
        "tripping characteristic",
        "trip curve",
        "характеристика расцепления",
    ),
    "protection_degree": (
        "protection degree",
        "степень защиты",
        "степень защиты, обеспечиваемая оболочкой",
    ),
    "requires_earthing": ("requires earthing", "требуется заземление"),
    "input_voltage": (
        "input voltage",
        "входное напряжение",
        "напряжение входа",
        "напряжение питания",
        "напряжение электропитания",
    ),
    "output_voltage": ("output voltage", "выходное напряжение", "напряжение выхода"),
    "rated_voltage": ("rated voltage", "номинальное напряжение"),
    "input_current": (
        "input current",
        "входной ток",
        "ток потребления",
        "потребляемый ток",
    ),
    "output_current": ("output current", "выходной ток"),
    "rated_current": ("rated current", "номинальный ток"),
    "power": ("power", "мощность"),
    "frequency": ("frequency", "частота"),
    "ambient_temperature": (
        "ambient temperature",
        "температура окружающей среды",
        "рабочая температура",
        "температурный диапазон",
    ),
}
_UNIT = (
    r"(?:м³/ч|м3/ч|л/с|м³|м3|MPa|МПа|kPa|кПа|bar|бар|kN|кН|м|m|"
    r"mV|kV|V|mA|A|kW|W|kHz|Hz|°C|℃|мм|mm|мВ|кВ|В|мА|А|кВт|Вт|кГц|Гц|°С|%)"
)
_NUMBER = r"[+-]?\d+(?:[.,]\d+)?"
_VALUE = re.compile(
    rf"(?P<value>(?:от\s+)?{_NUMBER}(?:\s*(?:\.{{2,}}|–|—|-|~|to|до)\s*{_NUMBER})?)"
    rf"\s*(?P<unit>{_UNIT})(?![\w-])",
    re.IGNORECASE,
)


def _numeric_text(text: str) -> str:
    """Разворачивает диапазон с одинаковыми единицами у обоих пределов, сохраняя исходную цитату отдельно."""
    return re.sub(
        rf"({_NUMBER})\s*({_UNIT})\s*(до|~|to|–|—)\s*({_NUMBER})\s*\2(?![\w-])",
        r"\1 \3 \4 \2",
        text,
        flags=re.I,
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
            rf"|(?:parameter|параметр)\s*\|\s*{marker}"
            rf"|(?:[^\d|]{{1,80}}\s+)?{marker}",
            line.strip(),
            re.IGNORECASE,
        )
        is not None
    )


def _scoped_lines(
    text: str,
    model: str,
    variant: str,
) -> list[tuple[str, str, str, str, str]]:
    """Привязывает соседние строки таблицы только к явному заголовку одной модели."""
    result: list[tuple[str, str, str, str, str]] = []
    heading = ""
    remaining = 0
    column_index = -1
    column_count = 0
    label_column = 0
    channel = ""
    output_section = False
    target = " ".join(part for part in (model.strip(), variant.strip()) if part)
    raw_lines = text.splitlines()
    pending = ""
    pending_property = ""
    for line_index, raw in enumerate(raw_lines):
        line = " ".join(raw.split())[:8000]
        joined_value = False
        if not line:
            continue
        if pending:
            if (
                re.fullmatch(
                    rf"{_VALUE.pattern}(?:\s+(?:AC|DC|AC/DC))?",
                    _numeric_text(line),
                    re.I,
                )
                or parse_discrete_value(pending_property, line) is not None
            ):
                line = pending + " " + line
                joined_value = True
            pending = ""
        label_property = next(
            (
                kind
                for kind, labels in _PROPERTY_LABELS.items()
                if any(label in line.casefold() for label in labels)
            ),
            "",
        )
        if (
            remaining
            and label_property
            and not _VALUE.search(_numeric_text(line))
            and len(line) <= 200
        ):
            # Готовые дискретные значения уже стоят после метки и не ждут новой строки.
            label_end = max(
                (
                    line.casefold().find(label) + len(label)
                    for label in _PROPERTY_LABELS[label_property]
                    if label in line.casefold()
                ),
                default=0,
            )
            if parse_discrete_value(label_property, line[label_end:]) is not None:
                pass
            else:
                pending_property = label_property
                pending = line
                continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) >= 3 and (
            cells[0].casefold()
            in {
                "parameter",
                "параметр",
                "model",
                "модель",
            }
            or re.search(r"\bmodel\b", cells[0], re.I)
        ):
            matches = [
                index
                for index, cell in enumerate(cells[1:], start=1)
                if cell.casefold() == target.casefold()
            ]
            heading = line if len(matches) == 1 else ""
            column_index = matches[0] if len(matches) == 1 else -1
            column_count = len(cells)
            model_columns = [
                i
                for i, cell in enumerate(cells[1:], 1)
                if len(cell) < 80
                and re.search(r"\d", cell)
                and re.search(r"[a-zа-я]", cell, re.I)
            ]
            label_column = max(0, min(model_columns, default=1) - 1)
            channel, output_section = "", False
            remaining = 80 if heading else 0
            continue
        if _single_model_heading(line, model, variant):
            # Плоский PDF-текст перечисляет заголовки соседних колонок подряд.
            # Последняя маркировка не делает первое числовое значение её строкой.
            neighbors = (
                raw_lines[max(0, line_index - 2) : line_index]
                + raw_lines[line_index + 1 : line_index + 3]
            )
            ambiguous_bare = line.casefold() == target.casefold() and any(
                re.fullmatch(r"(?=[\w.-]*\d)[^\W\d_][\w.-]{2,}", other.strip())
                and not _contains_exact(other, model)
                for other in neighbors
            )
            if ambiguous_bare:
                heading, remaining, column_index = "", 0, -1
                continue
            heading = line
            remaining = 80
            column_index = -1
            channel, output_section = "", False
            continue
        if re.match(
            r"^(?:(?:model|модель)\s*[:№#-]|(?:parameter|параметр)\s*\|)",
            line,
            re.I,
        ) or (
            remaining
            and (
                re.fullmatch(r"(?=[\w.-]*\d)[^\W\d_][\w.-]{2,}", line)
                or (
                    "-" in model
                    and line.casefold().startswith(model.casefold().split("-")[0] + "-")
                    and re.fullmatch(r"[\w.-]+", line)
                    and not _contains_exact(line, model)
                )
            )
        ):
            heading = ""
            remaining = 0
            column_index = -1
            channel, output_section = "", False
        if (
            remaining
            and column_index > 0
            and len(cells) == column_count
            and cells[label_column].casefold() == "output number"
        ):
            channel = cells[column_index]
            output_section = True
            continue
        property_label = cells[label_column] if len(cells) == column_count else cells[0]
        if any(
            cell.casefold() in {"input", "protection", "environment", "safety"}
            for cell in cells[:label_column]
        ):
            output_section, channel = False, ""
        # Роль задана явным заголовком OUTPUT NUMBER, а не величиной напряжения.
        output_labels = {
            "dc voltage": "Output voltage DC",
            "rated current": "Output current",
            "current range": "Output current",
            "rated power": "Power",
            "voltage adj. range": "Output voltage",
        }
        modes = {
            "dc voltage": "nominal",
            "rated current": "rated",
            "current range": "range",
            "rated power": "rated",
            "voltage adj. range": "adjustment",
        }
        mode = modes.get(property_label.casefold(), "") if output_section else ""
        if output_section:
            property_label = output_labels.get(
                property_label.casefold(), property_label
            )
        if (
            remaining
            and column_index > 0
            and len(cells) == column_count
            and any(
                label in property_label.casefold()
                for labels in _PROPERTY_LABELS.values()
                for label in labels
            )
        ):
            scoped = f"{target} | {property_label} | {cells[column_index]}"
            result.append((scoped, "exact_model_column", heading, channel, mode))
        elif _contains_exact(line, model) and (
            not variant or _contains_exact(line, variant)
        ):
            applicability = (
                "single_model_block"
                if joined_value
                else "ambiguous_model_line"
                if _ambiguous_model_line(line, model)
                else "exact_model_line"
            )
            result.append((line, applicability, "", "", ""))
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
            result.append((line, "single_model_block", heading, "", ""))
        if remaining:
            remaining -= 1
    return result


def extract_equipment_facts(
    *,
    source_id: str,
    model: str,
    variant: str,
    pages: list[dict[str, Any]],
    reference_mode: bool = False,
) -> list[dict[str, Any]]:
    """Принимает лишь строки, где модель и характеристика подтверждены вместе."""
    if not source_id or not model:
        return []
    extracted: list[dict[str, Any]] = []
    for page in pages[:40]:
        number = int(page.get("page", 0))
        text = str(page.get("text") or "")[:200_000]
        scoped = (
            [
                (line[:8000], "technical_reference", "", "", "")
                for line in text.splitlines()[:2000]
            ]
            if reference_mode
            else _scoped_lines(text, model, variant)
        )
        for line, applicability, heading, channel, mode in scoped:
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
                covering = [
                    (kind, found.end() - found.start())
                    for kind, names in _PROPERTY_LABELS.items()
                    for name in names
                    if (found := re.search(re.escape(name), line, re.I)) is not None
                    and found.start() <= label_match.start()
                    and found.end() >= label_match.end()
                ]
                preferred = max(covering, key=lambda pair: pair[1])[0]
                if preferred != property_type:
                    continue
                discrete = parse_discrete_value(
                    property_type, line[label_match.end() :]
                )
                if discrete is not None:
                    value_type, normalized, value = discrete
                    unit = ""
                else:
                    matches = list(
                        _VALUE.finditer(_numeric_text(line[label_match.end() :]))
                    )
                    if len(matches) != 1:
                        continue
                    match = matches[0]
                    value = match.group("value")
                    unit = match.group("unit")
                    normalized_range = re.sub(r"(?<=\d)-(?=\d)", "...", value)
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
                    if re.search(r"\bDC\b|постоянн\w*\s+ток", line, re.I)
                    else "AC"
                    if re.search(r"\bAC\b|переменн\w*\s+ток", line, re.I)
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
                    mode=mode,
                    condition=line if _conditional(line) else "",
                    variant=variant,
                    configuration=channel,
                    document_source_id=source_id,
                    page=number,
                    table_title=heading,
                    row_label=line,
                    column_label=model if heading else "",
                    snippet=line,
                    confidence=(
                        0.6
                        if reference_mode
                        else 0.7
                        if _conditional(line) or applicability == "ambiguous_model_line"
                        else 0.9
                        if applicability in {"exact_model_line", "exact_model_column"}
                        else 0.8
                    ),
                    applicability=applicability,
                )
                extracted.append(asdict(fact))
    return extracted
