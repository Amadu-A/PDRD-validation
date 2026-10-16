# services/analysis-service/src/pdrd_analysis_service/domain/design_decisions.py

"""Реестр прочитанных проектных решений без генерации новых утверждений.

Отсутствие основания в фактах означает ограничение проверки, а не нарушение.
Исходные факты сохраняются целиком; идентичность не выводится из похожих названий.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    normalize_label,
    normalized_value,
    property_key,
    scope_key,
)
from pdrd_analysis_service.domain.engineering_assertion import engineering_object_key


def stable_id(prefix: str, identity: tuple) -> str:
    """Хеширует явную идентичность, сохраняя изоляцию документа и область решения."""
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return prefix + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """Исходный факт и способ проверки его привязки к доступной странице."""

    observation: LocatedDocumentFact
    grounding: str

    @property
    def evidence_id(self) -> str:
        """Использует действующий ID факта, не создавая второй нумерации страниц."""
        return self.observation.fact_id


@dataclass(frozen=True, slots=True)
class DesignDecision:
    """Одно принятое свойство объекта в конкретной системе, области и режиме."""

    decision_id: str
    object_key: str
    original_identifiers: tuple[str, ...]
    original_names: tuple[str, ...]
    decision_type: str
    parameter_role: tuple[str, ...]
    scope: tuple[str, ...]
    observations: tuple[DecisionEvidence, ...]
    purpose_evidence_ids: tuple[str, ...]
    design_input_ids: tuple[str, ...]
    calculation_ref_ids: tuple[str, ...]
    requirement_ref_ids: tuple[str, ...]
    completeness: tuple[tuple[str, str], ...]
    unresolved_dependencies: tuple[str, ...]

    def as_protocol(self) -> dict:
        """Возвращает воспроизводимый протокол; чтение факта не подтверждает проект."""
        evidence = [asdict(item) for item in self.observations]
        return {
            "decision_id": self.decision_id,
            "object": self.object_key,
            "original_identifiers": list(self.original_identifiers),
            "original_names": list(self.original_names),
            "decision_type": self.decision_type,
            "parameter_role": list(self.parameter_role),
            "scope": dict(
                zip(
                    (
                        "system",
                        "location",
                        "segment",
                        "operating_mode",
                        "condition",
                        "seismic_map",
                        "row",
                    ),
                    self.scope,
                    strict=True,
                )
            ),
            "accepted_parameters": [
                {
                    "evidence_id": item.evidence_id,
                    "value_raw": item.observation.fact.value_raw,
                    "unit_raw": item.observation.fact.unit_raw,
                    "property_name": item.observation.fact.property_name,
                    "column_label": item.observation.fact.column_label,
                }
                for item in self.observations
            ],
            "purpose_evidence_ids": list(self.purpose_evidence_ids),
            "design_input_ids": list(self.design_input_ids),
            "calculation_ref_ids": list(self.calculation_ref_ids),
            "requirement_ref_ids": list(self.requirement_ref_ids),
            "evidence_ids": [item.evidence_id for item in self.observations],
            "evidence": evidence,
            "completeness": dict(self.completeness),
            "unresolved_dependencies": list(self.unresolved_dependencies),
            "checks": [
                {
                    "check_type": "decision_justification",
                    "status": "not_evaluable",
                    "reason_code": "assessment_not_performed",
                    "missing_data": list(self.unresolved_dependencies),
                }
            ],
        }


@dataclass(frozen=True, slots=True)
class DecisionRegistry:
    """Снимок одного документа; совпавшие теги других заданий сюда не попадают."""

    document_id: str
    document_context_enabled: bool
    decisions: tuple[DesignDecision, ...]
    evidence: tuple[DecisionEvidence, ...]
    unassigned_evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict:
        """Возвращает полный снимок для предметной приёмки без HTTP или новой БД."""
        return {
            "version": 1,
            "document_id": self.document_id,
            "document_context_enabled": self.document_context_enabled,
            "decisions": [item.as_protocol() for item in self.decisions],
            "evidence": [asdict(item) for item in self.evidence],
            "unassigned_evidence_ids": list(self.unassigned_evidence_ids),
            "tables": self.table_observations(),
        }

    def table_observations(self) -> list[dict]:
        """Сохраняет строки и продолжения; полнота листа не равна полноте таблицы."""
        tables: dict[tuple, list[DecisionEvidence]] = {}
        for item in self.evidence:
            fact = item.observation.fact
            if fact.table_id:
                key = (normalize_label(fact.table_id), *scope_key(fact)[:6])
                if not self.document_context_enabled:
                    key = (*key, item.observation.page)
                tables.setdefault(key, []).append(item)
        return [
            {
                "table_key": stable_id("table-", (self.document_id, *key)),
                "table_ids": list(
                    dict.fromkeys(item.observation.fact.table_id for item in members)
                ),
                "observed_pages": sorted({item.observation.page for item in members}),
                "completeness": "ambiguous",
                "reason_code": "table_extent_not_established",
                "cells": [
                    {
                        "evidence_id": item.evidence_id,
                        "page": item.observation.page,
                        "row_label": item.observation.fact.row_label,
                        "column_label": item.observation.fact.column_label,
                        "unit_raw": item.observation.fact.unit_raw,
                        "value_raw": item.observation.fact.value_raw,
                        "continuation_marker": item.observation.fact.continuation_marker,
                        "source_row_ids": list(item.observation.fact.source_row_ids),
                    }
                    for item in members
                ],
            }
            for key, members in sorted(tables.items())
        ]


def build_decision_registry(
    *,
    document_id: str,
    facts: tuple[LocatedDocumentFact, ...],
    page_texts: dict[int, str],
    document_context_enabled: bool = True,
) -> DecisionRegistry:
    """Группирует только точный объект, свойство и scope, не вызывая модель или retrieval."""
    if not document_id.strip():
        raise ValueError("Реестру требуется идентификатор текущего документа.")
    if len({item.fact_id for item in facts}) != len(facts):
        raise ValueError("В реестре повторены идентификаторы исходных фактов.")
    normalized_texts = {
        page: normalize_label(text) for page, text in page_texts.items()
    }
    evidence = tuple(
        DecisionEvidence(item, _grounding(item, normalized_texts)) for item in facts
    )
    groups: dict[tuple, list[DecisionEvidence]] = {}
    unassigned = []
    for item in evidence:
        fact = item.observation.fact
        obj, prop = engineering_object_key(fact), property_key(fact)
        if not obj or not prop:
            unassigned.append(item.evidence_id)
            continue
        scope = scope_key(fact)
        # При D=false разрешено только локальное прочтение. Не связываем листы.
        role = _parameter_role(item)
        identity = (
            obj,
            prop,
            scope,
            role,
            _local_page(item, document_context_enabled),
        )
        groups.setdefault(identity, []).append(item)

    by_object: dict[tuple, list[DecisionEvidence]] = {}
    for item in evidence:
        fact = item.observation.fact
        key = (engineering_object_key(fact), scope_key(fact)[:6])
        key = (*key, _local_page(item, document_context_enabled))
        by_object.setdefault(key, []).append(item)

    decisions = []
    global_purposes: dict[str, list[DecisionEvidence]] = {}
    for item in evidence:
        fact = item.observation.fact
        scope = scope_key(fact)
        if (
            property_key(fact).endswith(":system_purpose")
            and not any(scope[1:6])
            and (not scope[0] or scope[0] == normalize_label(fact.identifier))
        ):
            global_purposes.setdefault(engineering_object_key(fact), []).append(item)
    for identity, members in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        obj, prop, scope, role, page = identity
        related_key = (obj, scope[:6], page)
        related = [*by_object[related_key]]
        related.extend(
            item
            for item in global_purposes.get(obj, ())
            if item not in related
            and (
                document_context_enabled
                or item.observation.page == members[0].observation.page
            )
        )

        purpose = tuple(
            item.evidence_id
            for item in related
            if property_key(item.observation.fact).endswith(":system_purpose")
            or item.observation.fact.property_type == "purpose"
        )
        inputs = _reference_ids(related, {"design_input", "input_for"})
        calculations = _reference_ids(related, {"calculation_reference"})
        requirements = _reference_ids(related, {"requirement_reference"})
        completeness = (
            (
                "identity",
                "ambiguous"
                if page is not None and document_context_enabled
                else "observed",
            ),
            (
                "accepted_parameters",
                "observed"
                if all(item.grounding != "ambiguous" for item in members)
                else "ambiguous",
            ),
            ("purpose", "observed" if purpose else "not_observed"),
            ("design_inputs", "observed" if inputs else "not_observed"),
            ("calculation_refs", "observed" if calculations else "not_observed"),
            ("requirement_refs", "observed" if requirements else "not_observed"),
            (
                "related_documents",
                "not_observed" if document_context_enabled else "unavailable",
            ),
        )
        decisions.append(
            DesignDecision(
                stable_id("decision-", (document_id, *identity)),
                obj,
                tuple(
                    dict.fromkeys(item.observation.fact.identifier for item in members)
                ),
                tuple(
                    dict.fromkeys(
                        item.observation.fact.subject_name for item in members
                    )
                ),
                prop,
                role,
                scope,
                tuple(members),
                purpose,
                inputs,
                calculations,
                requirements,
                completeness,
                tuple(name for name, state in completeness if state != "observed"),
            )
        )
    return DecisionRegistry(
        document_id,
        document_context_enabled,
        tuple(decisions),
        evidence,
        tuple(unassigned),
    )


def _grounding(item: LocatedDocumentFact, page_texts: dict[int, str]) -> str:
    """Проверяет дословность или наличие области, сохраняя неоднозначные факты отдельно."""
    fact = item.fact
    if fact.source_pages and item.page not in fact.source_pages:
        return "ambiguous"
    quote = normalize_label(fact.evidence_text)
    text = page_texts.get(item.page, "")
    if quote and quote in text:
        numeric = normalize_label(fact.value_raw).replace(",", ".")
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", numeric) and not re.search(
            r"(?<![\w.])" + re.escape(numeric) + r"(?![\w.])", quote.replace(",", ".")
        ):
            return "ambiguous"
        return "literal_text"
    if fact.visual_regions and fact.extraction_method not in {"pdf_text", "pdf_table"}:
        return "visual_observation"
    return "ambiguous"


def _reference_ids(related: list[DecisionEvidence], types: set[str]) -> tuple[str, ...]:
    """Различает наблюдение ссылки и последующую проверку применимости основания."""
    return tuple(
        item.evidence_id
        for item in related
        if item.observation.fact.property_type in types
        or item.observation.fact.relation in types
    )


def _parameter_role(item: DecisionEvidence) -> tuple[str, ...]:
    """Не объединяет номинальные/расчётные значения и разные определения диаметра."""
    fact = item.observation.fact
    label = normalize_label(fact.property_name + " " + fact.column_label)
    markers = tuple(
        sorted(
            set(
                re.findall(
                    r"номиналь\w*|расчетн\w*|средн\w*|максим\w*|миним\w*|требуем\w*|гарантирован\w*|полезн\w*|общ\w*",
                    label,
                )
            )
        )
    )
    normalized = normalized_value(fact)
    diameter = (
        normalized[0]
        if normalized and normalized[0] in {"nominal_diameter", "outer_diameter"}
        else ""
    )
    column = normalize_label(fact.column_label)
    return (*markers, diameter, "column:" + column if column else "")


def _local_page(item: DecisionEvidence, document_context_enabled: bool) -> int | None:
    """Локальное обозначение аппарата без шкафа не устанавливает межстраничную идентичность."""
    fact = item.observation.fact
    unqualified_device = (
        normalize_label(fact.subject_type)
        in {"equipment", "terminal", "relay", "controller", "device"}
        and "/" not in fact.identifier
        and not fact.scope_location
    )
    return (
        item.observation.page
        if not document_context_enabled or unqualified_device
        else None
    )
