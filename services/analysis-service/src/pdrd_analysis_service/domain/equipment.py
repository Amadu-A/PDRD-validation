# services/analysis-service/src/pdrd_analysis_service/domain/equipment.py

"""Реестр физического оборудования и осторожное сопоставление EQ Facts."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any


def _token(value: str) -> str:
    """Нормализует ключ без восстановления отсутствующих обозначений."""
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class EquipmentOccurrence:
    """Физическое вхождение модели на конкретном чертеже."""

    page: int
    object_ref: str
    evidence_text: str
    visual_regions: tuple[dict[str, Any], ...]
    parameters: tuple[dict[str, Any], ...]
    article: str = ""
    equipment_type: str = ""


@dataclass(frozen=True, slots=True)
class InventoryItem:
    """Уникальная модель и все её проектные вхождения."""

    manufacturer: str
    model: str
    variant: str
    status: str
    confidence: float
    occurrences: tuple[EquipmentOccurrence, ...]

    @property
    def key(self) -> tuple[str, str, str]:
        """Возвращает ключ для единственного внешнего поиска."""
        return (_token(self.manufacturer), _token(self.model), _token(self.variant))


def build_inventory(pages: Sequence[Mapping[str, Any]]) -> tuple[InventoryItem, ...]:
    """Группирует вхождения без потери листа, параметров и областей."""
    groups: dict[tuple[str, str, str], list[tuple[dict, EquipmentOccurrence]]] = {}
    for page in pages:
        page_number = int(page.get("page_number", 0))
        facts = page.get("facts", page)
        if not isinstance(facts, Mapping):
            continue
        identities = facts.get("equipment_identities", [])
        if not isinstance(identities, list):
            continue
        for identity in identities:
            if not isinstance(identity, dict):
                continue
            manufacturer = str(identity.get("manufacturer") or "").strip()
            model = str(identity.get("model") or "").strip()
            variant = str(identity.get("variant") or "").strip()
            key = (_token(manufacturer), _token(model), _token(variant))
            occurrence = EquipmentOccurrence(
                page=page_number,
                object_ref=str(identity.get("object_ref") or ""),
                evidence_text=str(identity.get("evidence_text") or ""),
                visual_regions=tuple(identity.get("visual_regions") or ()),
                parameters=tuple(identity.get("parameters") or ()),
                article=str(identity.get("article") or ""),
                equipment_type=str(identity.get("equipment_type") or ""),
            )
            # Неполные обозначения остаются отдельными неоднозначными объектами.
            if not manufacturer or not model:
                key = (*key, str(page_number), occurrence.object_ref)
            groups.setdefault(key, []).append((identity, occurrence))

    inventory: list[InventoryItem] = []
    for entries in groups.values():
        first = entries[0][0]
        confidence = min(float(raw.get("confidence") or 0) for raw, _ in entries)
        resolved = (
            all(raw.get("status") == "resolved" for raw, _ in entries)
            and confidence >= 0.75
        )
        inventory.append(
            InventoryItem(
                manufacturer=str(first.get("manufacturer") or "").strip(),
                model=str(first.get("model") or "").strip(),
                variant=str(first.get("variant") or "").strip(),
                status="resolved" if resolved else "needs_review",
                confidence=confidence,
                occurrences=tuple(occurrence for _, occurrence in entries),
            )
        )

    # Разные исполнения одного обозначения у одного объекта нельзя сливать.
    variants: dict[tuple[str, str, str], set[str]] = {}
    for item in inventory:
        for occurrence in item.occurrences:
            if occurrence.object_ref:
                base = (*item.key[:2], _token(occurrence.object_ref))
                variants.setdefault(base, set()).add(item.key[2])
    ambiguous = {base for base, values in variants.items() if len(values) > 1}
    return tuple(
        InventoryItem(
            manufacturer=item.manufacturer,
            model=item.model,
            variant=item.variant,
            status=(
                "needs_review"
                if any(
                    (*item.key[:2], _token(occurrence.object_ref)) in ambiguous
                    for occurrence in item.occurrences
                    if occurrence.object_ref
                )
                else item.status
            ),
            confidence=item.confidence,
            occurrences=item.occurrences,
        )
        for item in inventory
    )


@dataclass(frozen=True, slots=True)
class EquipmentFact:
    """Характеристика из сохранённого документа с точным provenance."""

    property_name: str
    value_raw: str
    unit_raw: str
    role: str
    current_type: str
    mode: str
    variant: str
    source_id: str
    source_page: int
    snippet: str
    document_revision: str = ""
    table_title: str = ""
    row_label: str = ""
    column_label: str = ""
    confidence: float = 1.0
    applicability: str = "exact_model_line"
    phase: str = ""
    condition: str = ""
    configuration: str = ""
    value_type: str = "number"


@dataclass(frozen=True, slots=True)
class Comparison:
    """Детерминированный результат до общего Finalization."""

    candidate_id: str
    status: str
    page: int
    object_ref: str
    property_name: str
    project_value: str
    project_unit: str
    manufacturer_value: str
    manufacturer_unit: str
    source_id: str
    source_page: int
    explanation: str
    project_evidence: str
    manufacturer_evidence: str
    visual_regions: tuple[dict[str, Any], ...]


_PROPERTY_ALIASES = {
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
_UNIT_SCALE = {
    "мв": ("voltage", Decimal("0.001")),
    "в": ("voltage", Decimal(1)),
    "кв": ("voltage", Decimal(1000)),
    "mv": ("voltage", Decimal("0.001")),
    "v": ("voltage", Decimal(1)),
    "kv": ("voltage", Decimal(1000)),
    "ма": ("current", Decimal("0.001")),
    "ma": ("current", Decimal("0.001")),
    "а": ("current", Decimal(1)),
    "a": ("current", Decimal(1)),
    "вт": ("power", Decimal(1)),
    "w": ("power", Decimal(1)),
    "квт": ("power", Decimal(1000)),
    "kw": ("power", Decimal(1000)),
    "гц": ("frequency", Decimal(1)),
    "hz": ("frequency", Decimal(1)),
    "khz": ("frequency", Decimal(1000)),
    "кгц": ("frequency", Decimal(1000)),
    "°c": ("temperature", Decimal(1)),
    "°с": ("temperature", Decimal(1)),
}
_NUMBER = r"[+-]?\d+(?:[.,]\d+)?"


def _property(value: str) -> str:
    """Принимает только известные явно различимые параметры."""
    text = _token(value)
    for kind, aliases in _PROPERTY_ALIASES.items():
        if text == kind or any(alias in text for alias in aliases):
            return kind
    return ""


def _current_type(value: str) -> str:
    """Различает AC/DC без вывода типа по величине напряжения."""
    text = _token(value)
    if "ac/dc" in text or "dc/ac" in text:
        return "acdc"
    if text in {"ac", "переменный", "переменного тока"}:
        return "ac"
    if text in {"dc", "постоянный", "постоянного тока"}:
        return "dc"
    return ""


def _phase(value: str) -> str:
    """Сопоставляет только прямо указанную фазность."""
    if value.casefold() in {"single", "three"}:
        return value.casefold()
    if re.search(r"\b(?:single|1)[-\s]?phase\b|однофаз", value, re.I):
        return "single"
    if re.search(r"\b(?:three|3)[-\s]?phase\b|тр[её]хфаз", value, re.I):
        return "three"
    return ""


def _quantity(value: str, unit: str) -> tuple[str, Decimal, Decimal] | None:
    """Разбирает скаляр или закрытый допустимый диапазон."""
    unit_info = _UNIT_SCALE.get(_token(unit).replace(" ", ""))
    if unit_info is None:
        return None
    clean = value.strip().replace("−", "-")
    clean = re.sub(r"(?<=\d)-(?=\d)", "...", clean)
    if not re.fullmatch(
        rf"{_NUMBER}(?:\s*(?:\.{{2,}}|–|—|\s-\s|to)\s*{_NUMBER})?", clean, re.I
    ):
        return None
    numbers = re.findall(_NUMBER, clean)
    if not numbers or len(numbers) > 2:
        return None
    try:
        values = [
            Decimal(number.replace(",", ".")) * unit_info[1] for number in numbers
        ]
    except InvalidOperation:
        return None
    if len(values) == 1:
        return unit_info[0], values[0], values[0]
    if values[0] > values[1]:
        return None
    if not re.search(r"(?:\.{2,}|–|—|\s-\s|\bto\b)", clean, re.I):
        return None
    return unit_info[0], min(values), max(values)


def _value(value: str, unit: str, property_name: str) -> tuple | None:
    """Разбирает только известный тип свойства и совместимые единицы."""
    if property_name in {"operating_mode", "contact_configuration"}:
        raw = _token(value)
        if unit or not raw or len(raw) > 120:
            return None
        members = frozenset((raw,))
        return property_name, members, members
    if property_name not in {"trip_curve", "protection_degree", "requires_earthing"}:
        quantity = _quantity(value, unit)
        expected = (
            "voltage"
            if property_name.endswith("voltage")
            else "current"
            if property_name.endswith("current")
            else "temperature"
            if property_name == "ambient_temperature"
            else property_name
        )
        return quantity if quantity is not None and quantity[0] == expected else None
    if unit:
        return None
    raw = value.strip().casefold()
    if property_name == "trip_curve":
        members = frozenset(re.split(r"\s*[,;/]\s*", raw))
        if members and members <= {"b", "c", "d", "k", "z"}:
            return property_name, members, members
    if property_name == "protection_degree" and re.fullmatch(r"ip\d{2}", raw):
        members = frozenset((raw,))
        return property_name, members, members
    if property_name == "requires_earthing":
        aliases = {
            "true": "true",
            "yes": "true",
            "да": "true",
            "required": "true",
            "false": "false",
            "no": "false",
            "нет": "false",
            "not required": "false",
        }
        if raw in aliases:
            members = frozenset((aliases[raw],))
            return property_name, members, members
    return None


def _fits(project: tuple, maker: tuple) -> bool:
    """Перечисления сравнивает по включению, физические значения по диапазону."""
    if isinstance(maker[1], frozenset):
        return isinstance(project[1], frozenset) and project[1] <= maker[1]
    return maker[1] <= project[1] and project[2] <= maker[2]


def compare_equipment(
    item: InventoryItem,
    facts: Sequence[EquipmentFact],
) -> tuple[Comparison, ...]:
    """Сравнивает только точную модель, режим, роль и совместимые единицы."""
    if item.status != "resolved":
        return ()
    comparisons: list[Comparison] = []
    for occurrence in item.occurrences:
        for parameter in occurrence.parameters:
            if not isinstance(parameter, dict):
                continue
            property_name = _property(str(parameter.get("property_name") or ""))
            if not property_name:
                continue
            project_value = str(parameter.get("value_raw") or "")
            project_unit = str(parameter.get("unit_raw") or "")
            project_quantity = _value(project_value, project_unit, property_name)
            if project_quantity is None:
                continue
            for fact in facts:
                if _property(fact.property_name) != property_name:
                    continue
                if fact.variant and _token(fact.variant) != item.key[2]:
                    continue
                project_role = _token(str(parameter.get("role") or ""))
                if fact.role and project_role != _token(fact.role):
                    continue
                project_current = _current_type(
                    str(parameter.get("current_type") or "")
                )
                fact_current = _current_type(fact.current_type)
                if fact_current and project_current != fact_current:
                    continue
                if fact.mode and _token(str(parameter.get("mode") or "")) != _token(
                    fact.mode
                ):
                    continue
                if fact.phase:
                    project_phase = _phase(
                        " ".join(
                            str(parameter.get(field) or "")
                            for field in ("phase", "configuration", "evidence_text")
                        )
                    )
                    if project_phase != fact.phase:
                        continue
                if fact.configuration and _token(
                    str(parameter.get("configuration") or "")
                ) != _token(fact.configuration):
                    continue
                maker_quantity = _value(fact.value_raw, fact.unit_raw, property_name)
                if maker_quantity is None or project_quantity[0] != maker_quantity[0]:
                    continue
                maker_low, maker_high = maker_quantity[1:]
                conflicting = any(
                    other is not fact
                    and _property(other.property_name) == property_name
                    and (not other.variant or _token(other.variant) == item.key[2])
                    and _token(other.role) == _token(fact.role)
                    and _current_type(other.current_type) == fact_current
                    and _token(other.mode) == _token(fact.mode)
                    and other.phase == fact.phase
                    and _token(other.configuration) == _token(fact.configuration)
                    and _token(other.condition) == _token(fact.condition)
                    and (
                        other_quantity := _value(
                            other.value_raw, other.unit_raw, property_name
                        )
                    )
                    is not None
                    and other_quantity[0] == maker_quantity[0]
                    and other_quantity[1:] != maker_quantity[1:]
                    for other in facts
                )
                if conflicting:
                    status = "needs_review"
                    explanation = "Документация содержит противоречивые значения одной характеристики."
                elif (
                    fact.condition
                    or parameter.get("condition")
                    or fact.confidence < 0.85
                    or fact.applicability
                    not in {
                        "exact_model_line",
                        "exact_model_column",
                    }
                ):
                    status = "needs_review"
                    explanation = "Применимость характеристики требует проверки таблицы производителя."
                elif _fits(project_quantity, maker_quantity):
                    status = "matched"
                    explanation = "Проектное значение соответствует подтверждённым значениям производителя."
                elif fact.value_type == "text":
                    status = "needs_review"
                    explanation = (
                        "Различие текстовых характеристик требует смысловой проверки."
                    )
                elif maker_low == maker_high and isinstance(maker_low, Decimal):
                    status = "needs_review"
                    explanation = "Скалярное значение производителя требует проверки применимости."
                else:
                    status = "mismatch_candidate"
                    explanation = "Проектное значение не входит в подтверждённые значения производителя."
                stable = "|".join(
                    (
                        *item.key,
                        str(occurrence.page),
                        occurrence.object_ref,
                        property_name,
                        project_value,
                        fact.source_id,
                        fact.value_raw,
                        str(fact.source_page),
                        fact.role,
                        fact.current_type,
                        fact.phase,
                        fact.mode,
                        fact.condition,
                        fact.configuration,
                    )
                )
                comparisons.append(
                    Comparison(
                        candidate_id="EQ-" + sha256(stable.encode()).hexdigest()[:20],
                        status=status,
                        page=occurrence.page,
                        object_ref=occurrence.object_ref,
                        property_name=property_name,
                        project_value=project_value,
                        project_unit=project_unit,
                        manufacturer_value=fact.value_raw,
                        manufacturer_unit=fact.unit_raw,
                        source_id=fact.source_id,
                        source_page=fact.source_page,
                        explanation=explanation,
                        project_evidence=str(
                            parameter.get("evidence_text") or occurrence.evidence_text
                        ),
                        manufacturer_evidence=fact.snippet,
                        visual_regions=occurrence.visual_regions,
                    )
                )
    return tuple(comparisons)


def inventory_payload(items: Sequence[InventoryItem]) -> list[dict]:
    """Готовит JSON без потери физических вхождений."""
    return [asdict(item) for item in items]
