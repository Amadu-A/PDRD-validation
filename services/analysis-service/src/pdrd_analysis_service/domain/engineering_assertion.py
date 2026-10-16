# services/analysis-service/src/pdrd_analysis_service/domain/engineering_assertion.py

"""Единое инженерное утверждение: предмет, условия, исходные факты и способ проверки.

Проекция живёт в существующем origin_assertions. Категория и текст модели
не подтверждают вывод; доказанность назначает только проверяющий сценарий.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace

from pdrd_analysis_service.domain.document_context import (
    DocumentFact,
    LocatedDocumentFact,
    document_fact_from_mapping,
    normalize_label,
    normalized_value,
    property_key,
    scope_key,
    subject_key,
)

CLAIM_TYPES = (
    "arithmetic",
    "contradiction",
    "missing_justification",
    "documentation",
    "visual_connection",
)


@dataclass(frozen=True, slots=True)
class EngineeringAssertion:
    """Неизменяемый предмет проверки с происхождением каждого наблюдения."""

    object_key: str
    property_key: str
    claim_type: str
    conditions: tuple[str, ...]
    observations: tuple[LocatedDocumentFact, ...]
    verification: str = "unverified"
    verification_rule: str = ""

    @property
    def identity(self) -> tuple:
        """Отделяет независимые претензии к одному объекту."""
        return self.object_key, self.property_key, self.claim_type, self.conditions

    @property
    def assertion_id(self) -> str:
        """Связывает все этапы без зависимости от свободной формулировки модели."""
        evidence = sorted(_evidence_key(item) for item in self.observations)
        payload = json.dumps((self.identity, evidence), ensure_ascii=False)
        return "assertion-" + hashlib.sha256(payload.encode()).hexdigest()[:20]

    def as_origin(self) -> dict:
        """Сериализует модель в существующий контракт происхождения без новой БД."""
        return {
            "origin": "engineering_assertion",
            "assertion_id": self.assertion_id,
            **asdict(self),
        }


def assertion_from_facts(
    facts: tuple[LocatedDocumentFact, ...],
    *,
    claim_type: str = "contradiction",
    include_related: bool = False,
) -> EngineeringAssertion | None:
    """Формирует предмет только для сопоставимых свойств одного явно заданного объекта."""
    if not facts or claim_type not in CLAIM_TYPES:
        return None
    first = facts[0].fact
    obj = engineering_object_key(first)
    prop = property_key(first)
    if not obj and first.subject_name:
        obj = "named:" + normalize_label(first.subject_name)
    if not obj or not prop:
        return None
    compatible = tuple(
        item
        for item in facts
        if (
            engineering_object_key(item.fact)
            or "named:" + normalize_label(item.fact.subject_name)
        )
        == obj
        and property_key(item.fact) == prop
        and scope_key(item.fact) == scope_key(first)
    )
    return EngineeringAssertion(
        obj,
        prop,
        claim_type,
        scope_key(first),
        facts if include_related else compatible,
    )


def engineering_object_key(fact: DocumentFact) -> str:
    """Удерживает явный тег назначения системы при разных названиях её типа моделью."""
    if fact.identifier and property_key(fact).endswith(":system_purpose"):
        return "system:" + normalize_label(fact.identifier)
    return subject_key(fact)


def read_assertion(origins: tuple[dict, ...]) -> EngineeringAssertion | None:
    """Читает сериализованную проекцию, не повышая способ подтверждения."""
    for origin in origins:
        if origin.get("origin") != "engineering_assertion":
            continue
        try:
            observations = []
            for raw in origin["observations"]:
                fact = document_fact_from_mapping(raw["fact"])
                if fact is None or not isinstance(raw["page"], int) or raw["page"] < 1:
                    return None
                observations.append(
                    LocatedDocumentFact(raw["page"], raw["index"], fact)
                )
            assertion = EngineeringAssertion(
                origin["object_key"],
                origin["property_key"],
                origin["claim_type"],
                tuple(origin["conditions"]),
                tuple(observations),
                origin.get("verification", "unverified"),
                origin.get("verification_rule", ""),
            )
            if (
                assertion.property_key.endswith(":system_purpose")
                and observations
                and all(
                    property_key(item.fact) == "relationship:system_purpose"
                    for item in observations
                )
            ):
                assertion = replace(
                    assertion, property_key="relationship:system_purpose"
                )
            if observations and len(assertion.conditions) == 7:
                canonical = scope_key(observations[0].fact)
                if assertion.conditions[1:] == canonical[1:] and assertion.conditions[
                    0
                ] == normalize_label(observations[0].fact.identifier):
                    assertion = replace(assertion, conditions=canonical)
            if assertion.claim_type not in CLAIM_TYPES or not assertion.observations:
                return None
            return assertion
        except (KeyError, TypeError, ValueError):
            return None
    return None


def same_assertion(left: EngineeringAssertion, right: EngineeringAssertion) -> bool:
    """Объединяет только один предмет с пересекающимся исходным доказательством."""
    if left.identity != right.identity:
        return False
    if (
        left.verification == right.verification == "deterministic"
        and left.verification_rule == right.verification_rule == "engineering_program"
        and any(condition.startswith("program-check:") for condition in left.conditions)
    ):
        return True
    # Для неполного обоснования один принятый параметр может порождать несколько
    # независимых вопросов. Без отдельного доказательства тождества не объединяем их.
    if left.claim_type != "contradiction":
        return False
    if {scope_key(item.fact) for item in left.observations} != {
        scope_key(item.fact) for item in right.observations
    }:
        return False

    # A↔B и B↔C могут касаться разных противоречий одного объекта.
    # Пересечения одной записи недостаточно для тождества всей претензии.
    def propositions(assertion: EngineeringAssertion) -> set[tuple]:
        return {
            (
                engineering_object_key(item.fact),
                property_key(item.fact),
                scope_key(item.fact),
                normalized_value(item.fact),
            )
            for item in assertion.observations
        }

    if propositions(left) != propositions(right):
        return False

    from pdrd_analysis_service.domain.physical_identity import comparison_identity

    if any(
        normalized_value(item.fact) is None
        for item in (*left.observations, *right.observations)
    ):
        return False
    if comparison_identity((*left.observations, *right.observations)) not in {
        "not_required",
        "same_physical_object",
    }:
        return False

    def anchors(assertion: EngineeringAssertion) -> set[tuple]:
        return {_evidence_key(item) for item in assertion.observations}

    return bool(anchors(left) & anchors(right)) or (
        len(propositions(left)) > 1
        and any(
            first.page == second.page
            and normalized_value(first.fact) == normalized_value(second.fact)
            and (
                normalize_label(first.fact.evidence_text)
                in normalize_label(second.fact.evidence_text)
                or normalize_label(second.fact.evidence_text)
                in normalize_label(first.fact.evidence_text)
            )
            for first in left.observations
            for second in right.observations
        )
    )


def assertion_subsumes(
    complete: EngineeringAssertion, partial: EngineeringAssertion
) -> bool:
    """Поглощает лишь незавершённое сравнение; независимый вопрос обоснования остаётся."""
    if complete.identity != partial.identity or complete.claim_type != "contradiction":
        return False
    from pdrd_analysis_service.domain.physical_identity import comparison_identity

    if comparison_identity((*complete.observations, *partial.observations)) not in {
        "not_required",
        "same_physical_object",
    }:
        return False

    def propositions(assertion):
        """Сравнивает исходные стороны, а не формулировки кандидатов."""
        return {
            (
                engineering_object_key(item.fact),
                property_key(item.fact),
                scope_key(item.fact),
                normalized_value(item.fact),
            )
            for item in assertion.observations
        }

    full, part = propositions(complete), propositions(partial)
    return (
        len(full) > 1
        and bool(part)
        and part < full
        and all(value[-1] is not None for value in full)
    )


def _evidence_key(item: LocatedDocumentFact) -> tuple:
    """Различает одинаковые подписи в разных областях одной страницы."""
    regions = tuple(
        sorted(
            tuple(region[key] for key in ("x_min", "y_min", "x_max", "y_max"))
            for region in item.fact.visual_regions
        )
    )
    return (
        item.page,
        normalize_label(item.fact.evidence_text),
        item.fact.value_raw,
        item.fact.unit_raw,
        regions,
    )


def merge_assertions(
    left: EngineeringAssertion, right: EngineeringAssertion
) -> EngineeringAssertion:
    """Сохраняет все независимые исходные наблюдения после проверки идентичности."""
    if not same_assertion(left, right):
        raise ValueError("Объединять независимые инженерные претензии запрещено.")
    observations = list(left.observations)
    for item in right.observations:
        if item not in observations:
            observations.append(item)
    return replace(left, observations=tuple(observations))


def candidate_property(text: str) -> str:
    """Распознаёт лишь явный предмет для старого кандидата без ссылки на план."""
    for pattern, prop in (
        (r"толщин\w*\s+стен", "Толщина стенки"),
        (r"диаметр|типоразмер|\b(?:ду|dn)\s*\d", "Диаметр"),
        (r"длин|протяж", "Длина"),
        (r"водоотвед|назначени\w*\s+систем", "Назначение системы"),
        (r"напор", "Напор"),
        (r"отметк|высот", "Абсолютная отметка"),
        (r"расход", "Расход воды"),
    ):
        if re.search(pattern, text, re.I):
            return prop
    return ""


def candidate_parameter_roles(text: str) -> tuple[str, ...]:
    """Разделяет явно названные расчётные роли без смыслового угадывания."""
    text = normalize_label(text)
    return tuple(
        role
        for role in ("гарантирован", "требуем", "максим", "миним", "средн")
        if role in text
    )


def deterministic_unit_mismatch(assertion: EngineeringAssertion) -> bool:
    """Доказывает только пересчёт одной строки при известных единицах и одинаковых условиях."""
    from pdrd_analysis_service.domain.engineering_claims import extract_quantities

    facts = assertion.observations
    if assertion.claim_type != "contradiction" or len(facts) < 2:
        return False
    first = facts[0].fact
    if not first.table_id or not first.row_label:
        return False
    if any(
        item.fact.table_id != first.table_id
        or engineering_object_key(item.fact) != engineering_object_key(first)
        or item.fact.row_label != first.row_label
        or property_key(item.fact) != property_key(first)
        or scope_key(item.fact) != scope_key(first)
        for item in facts
    ):
        return False

    def roles(fact: DocumentFact) -> set[str]:
        return set(
            re.findall(
                r"средн\w*|максим\w*|миним\w*|требуем\w*|гарантирован\w*",
                normalize_label(fact.column_label + " " + fact.property_name),
            )
        )

    if any(roles(item.fact) != roles(first) for item in facts):
        return False
    values = [
        extract_quantities(f"{item.fact.value_raw} {item.fact.unit_raw}")
        for item in facts
    ]
    if not all(len(value) == 1 for value in values):
        return False
    baseline = values[0][0]
    return any(
        value[0].dimension == baseline.dimension
        and value[0].unit != baseline.unit
        and abs(value[0].value - baseline.value)
        > value[0].tolerance + baseline.tolerance
        for value in values[1:]
    )
