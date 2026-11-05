# services/analysis-service/src/pdrd_analysis_service/domain/equipment.py

"""Реестр физического оборудования и осторожное сопоставление EQ Facts."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from pdrd_analysis_service.domain.equipment_identification import identity_token


def _token(value: str) -> str:
    """Нормализует ключ без восстановления отсутствующих обозначений."""
    return " ".join(value.casefold().split())


def equipment_key(
    manufacturer: str, model: str, variant: str, article: str = "", reference: str = ""
) -> tuple[str, str, str]:
    """Сохраняет модель, исполнение и артикул при незначащих вариациях записи."""
    return (
        _token(manufacturer),
        identity_token(model),
        identity_token(variant)
        + ("/артикул" + identity_token(article) if article else "")
        + ("/стандарт" + identity_token(reference) if reference else ""),
    )


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
    source_row_ids: tuple[str, ...] = ()
    quantity: str = ""
    units: str = ""
    project_code: str = ""
    under: str = ""


@dataclass(frozen=True, slots=True)
class InventoryItem:
    """Уникальная модель и все её проектные вхождения."""

    manufacturer: str
    model: str
    variant: str
    status: str
    confidence: float
    occurrences: tuple[EquipmentOccurrence, ...]
    variant_kind: str = "execution"
    model_status: str = "confirmed"
    manufacturer_status: str = "explicit"
    article: str = ""
    item_type: str = "serial_equipment"
    search_name: str = ""
    search_reference: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        """Возвращает ключ для единственного внешнего поиска."""
        return equipment_key(
            self.manufacturer,
            self.model,
            self.variant,
            self.article,
            self.search_reference,
        )


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
            if identity.get("entity_kind") in {"port", "interface", "line", "symbol"}:
                continue
            manufacturer = str(identity.get("manufacturer") or "").strip()
            model = str(identity.get("model") or "").strip()
            variant = str(identity.get("variant") or "").strip()
            article = str(identity.get("article") or "").strip()
            key = equipment_key(
                manufacturer,
                model,
                variant,
                article,
                str(identity.get("search_reference") or ""),
            )
            occurrence = EquipmentOccurrence(
                page=page_number,
                object_ref=str(identity.get("object_ref") or ""),
                evidence_text=str(identity.get("evidence_text") or ""),
                visual_regions=tuple(identity.get("visual_regions") or ()),
                parameters=tuple(identity.get("parameters") or ()),
                article=str(identity.get("article") or ""),
                equipment_type=str(identity.get("equipment_type") or ""),
                source_row_ids=tuple(identity.get("source_row_ids") or ()),
                quantity=str(identity.get("quantity") or ""),
                units=str(identity.get("units") or ""),
                project_code=str(identity.get("project_code") or ""),
                under=str(identity.get("under") or ""),
            )
            # Неполные обозначения остаются отдельными неоднозначными объектами.
            if not model:
                key = (*key, str(page_number), occurrence.object_ref)
            if identity.get("extraction_method") == "pdf_table":
                regions = identity.get("source_regions") or []
                for source_page in sorted(
                    set(identity.get("source_pages") or [page_number])
                ):
                    local = [
                        region
                        for region in regions
                        if region.get("page") == source_page
                    ]
                    physical = replace(
                        occurrence,
                        page=int(source_page),
                        visual_regions=tuple(
                            {
                                name: region[name]
                                for name in ("x_min", "y_min", "x_max", "y_max")
                            }
                            for region in local
                        ),
                        source_row_ids=tuple(region["row_id"] for region in local),
                    )
                    groups.setdefault(key, []).append((identity, physical))
            else:
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
                variant_kind=str(first.get("variant_kind") or "execution"),
                model_status=(
                    "confirmed"
                    if all(
                        raw.get("model_status", "confirmed") == "confirmed"
                        for raw, _ in entries
                    )
                    else "needs_review"
                ),
                manufacturer_status=str(first.get("manufacturer_status") or "explicit"),
                article=str(first.get("article") or "").strip(),
                item_type=str(first.get("item_type") or "serial_equipment"),
                search_name=str(first.get("search_name") or ""),
                search_reference=str(first.get("search_reference") or ""),
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
        replace(
            item,
            status=(
                "needs_review"
                if any(
                    (*item.key[:2], _token(occurrence.object_ref)) in ambiguous
                    for occurrence in item.occurrences
                    if occurrence.object_ref
                )
                else item.status
            ),
            model_status=(
                "needs_review"
                if any(
                    (*item.key[:2], _token(occurrence.object_ref)) in ambiguous
                    for occurrence in item.occurrences
                    if occurrence.object_ref
                )
                else item.model_status
            ),
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
    manufacturer: str = ""
    model: str = ""
    article: str = ""


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
    project_code: str = ""
    under: str = ""
    applicability_status: str = "applicable"
    reason_code: str = ""
    mode: str = ""
    condition: str = ""
    configuration: str = ""


_PROPERTY_ALIASES = {
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
    "rtd_wires": ("rtd wires", "схема подключения rtd", "число проводов датчика"),
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
    "protection_degree": ("protection degree", "степень защиты"),
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
_UNIT_SCALE = {
    "м3": ("volume", Decimal(1)),
    "м³": ("volume", Decimal(1)),
    "м3/ч": ("flow_rate", Decimal(1)),
    "м³/ч": ("flow_rate", Decimal(1)),
    "л/с": ("flow_rate", Decimal("3.6")),
    "мпа": ("pressure", Decimal(1)),
    "mpa": ("pressure", Decimal(1)),
    "бар": ("pressure", Decimal("0.1")),
    "bar": ("pressure", Decimal("0.1")),
    "кпа": ("pressure", Decimal("0.001")),
    "kpa": ("pressure", Decimal("0.001")),
    "кн": ("load_capacity", Decimal(1)),
    "kn": ("load_capacity", Decimal(1)),
    "мм": ("length", Decimal(1)),
    "mm": ("length", Decimal(1)),
    "м": ("length", Decimal(1000)),
    "m": ("length", Decimal(1000)),
    "%": ("relative_percent", Decimal(1)),
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
    "℃": ("temperature", Decimal(1)),
}
_NUMBER = r"[+-]?\d+(?:[.,]\d+)?"


def _property(value: str) -> str:
    """Принимает только известные явно различимые параметры."""
    text = _token(value)
    for kind, aliases in _PROPERTY_ALIASES.items():
        if text == kind or any(alias in text for alias in aliases):
            return kind
    return ""


def equipment_property_name(value: str) -> str:
    """Возвращает единый предметный ключ свойства для отчёта отдельной позиции."""
    return _property(value)


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
    clean = re.sub(r"^от\s+", "", clean, flags=re.I)
    clean = re.sub(r"\s*(?:до|~)\s*", "...", clean, flags=re.I)
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
    if property_name in {"sensor_type", "rtd_wires", "accessory_model"}:
        if unit:
            return None
        members = frozenset(_token(part) for part in re.split(r"[,;]", value))
        if property_name == "sensor_type" and not members <= {
            "pt100",
            "pt500",
            "pt1000",
            "ni100",
            "cu50",
            "cu100",
        }:
            return None
        if property_name == "rtd_wires" and not members <= {"2", "3", "4"}:
            return None
        if property_name == "accessory_model" and any(
            not re.fullmatch(r"[\w./-]{1,64}", part) for part in members
        ):
            return None
        return (
            (property_name, members, members)
            if members and len(members) <= 16
            else None
        )
    if property_name in {"operating_mode", "contact_configuration"}:
        raw = _token(value)
        if unit or not raw or len(raw) > 120:
            return None
        members = frozenset((raw,))
        return property_name, members, members
    if property_name not in {"trip_curve", "protection_degree", "requires_earthing"}:
        quantity = _quantity(value, unit)
        expected = (
            "length"
            if property_name.startswith("mounting_")
            or property_name
            in {"diameter", "wall_thickness", "outer_diameter", "nominal_diameter"}
            else "voltage"
            if property_name.endswith("voltage")
            else "current"
            if property_name.endswith("current")
            else "temperature"
            if property_name == "ambient_temperature"
            else "pressure"
            if property_name.endswith("pressure")
            else "flow_rate"
            if property_name.endswith("flow_rate")
            else "volume"
            if property_name.endswith("volume")
            else property_name
        )
        if property_name == "measurement_error":
            return (
                quantity
                if quantity is not None
                and quantity[0] in {"temperature", "relative_percent"}
                else None
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
            if parameter.get("evidence_status") == "needs_review":
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
                if fact.variant and identity_token(fact.variant) != identity_token(
                    item.variant
                ):
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
                    and other.applicability
                    in {"exact_model_line", "exact_model_column"}
                    and other.confidence >= 0.85
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
                applicability_status = "applicable"
                reason_code = ""
                foreign_identity = any(
                    raw and identity_token(raw) != identity_token(expected)
                    for raw, expected in (
                        (fact.manufacturer, item.manufacturer),
                        (fact.model, item.model),
                        (fact.article, item.article),
                    )
                )
                if foreign_identity:
                    status = "not_comparable"
                    applicability_status = "not_proven"
                    reason_code = "fact_product_identity_mismatch"
                    explanation = (
                        "Факт относится к другому изготовителю, модели или артикулу."
                    )
                elif fact.applicability not in {
                    "exact_model_line",
                    "exact_model_column",
                }:
                    status = "not_comparable"
                    applicability_status = "not_proven"
                    reason_code = "fact_identity_not_proven"
                    explanation = "Справочный факт не подтверждает характеристику конкретного изделия."
                elif fact.confidence < 0.85:
                    status = "insufficient_evidence"
                    applicability_status = "not_proven"
                    reason_code = "fact_evidence_insufficient"
                    explanation = (
                        "Достоверность и применимость характеристики не подтверждены."
                    )
                elif conflicting:
                    status = "insufficient_evidence"
                    applicability_status = "ambiguous"
                    reason_code = "conflicting_document_values"
                    explanation = "Документация содержит противоречивые значения одной характеристики."
                elif _token(fact.condition) != _token(
                    str(parameter.get("condition") or "")
                ):
                    status = "needs_review"
                    applicability_status = "not_proven"
                    reason_code = "conditions_not_proven"
                    explanation = (
                        "Условия применения характеристики не сопоставлены с проектом."
                    )
                elif _fits(project_quantity, maker_quantity):
                    status = "matched"
                    explanation = "Проектное значение соответствует подтверждённым значениям производителя."
                elif (
                    fact.value_type == "text"
                    and property_name not in {"sensor_type", "accessory_model"}
                ) or property_name == "rtd_wires":
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
                        project_code=occurrence.project_code,
                        under=occurrence.under,
                        applicability_status=applicability_status,
                        reason_code=reason_code,
                        mode=fact.mode,
                        condition=fact.condition,
                        configuration=fact.configuration,
                    )
                )
    return tuple(comparisons)


def inventory_payload(items: Sequence[InventoryItem]) -> list[dict]:
    """Готовит JSON без потери физических вхождений."""
    return [asdict(item) for item in items]


def equipment_property_checks(
    item: InventoryItem,
    facts: Sequence[EquipmentFact],
    comparisons: Sequence[Comparison],
) -> list[dict]:
    """Отличает отсутствие параметра от невозможности сопоставить условия его проверки."""
    project = {
        _property(str(parameter.get("property_name") or ""))
        for part in item.occurrences
        for parameter in part.parameters
        if parameter.get("evidence_status") != "needs_review"
    }
    manufacturer = {_property(fact.property_name) for fact in facts}
    compared = {
        comparison.property_name
        for comparison in comparisons
        if comparison.applicability_status == "applicable"
    }
    return [
        {
            "property_name": prop,
            "status": "project_property_missing"
            if prop not in project
            else "manufacturer_property_missing"
            if prop not in manufacturer
            else "comparison_completed"
            if prop in compared
            else "conditions_not_comparable",
        }
        for prop in sorted((project | manufacturer) - {""})
    ]


def equipment_value_key(value: str, unit: str, property_name: str) -> tuple | None:
    """Канонизирует сравнимое значение без изменения исходной паспортной записи."""
    return _value(value, unit, property_name)
