# services/analysis-service/src/pdrd_analysis_service/application/use_cases/document_context.py

"""Ограниченный D-контекст страницы и проверка противоречий PDF."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from pdrd_analysis_service.application.candidate_trace import trace_candidate
from pdrd_analysis_service.application.decision_protocol import (
    attach_decision_protocols,
)
from pdrd_analysis_service.application.engineering_program import (
    EngineeringReport,
    audit_engineering_candidates,
    program_findings,
    run_engineering_program,
)
from pdrd_analysis_service.application.ports.vision_model import StructuredVisionModel
from pdrd_analysis_service.domain.analysis import (
    DocumentContextSource,
    FindingDraft,
    FindingVisualRegion,
)
from pdrd_analysis_service.domain.design_decisions import build_decision_registry
from pdrd_analysis_service.domain.document_context import (
    ConflictGroup,
    DocumentFact,
    LocatedDocumentFact,
    discover_conflicts,
    fact_search_text,
    normalize_label,
    normalized_value,
    subject_key,
)
from pdrd_analysis_service.domain.engineering_assertion import (
    assertion_from_facts,
    deterministic_unit_mismatch,
    merge_assertions,
    read_assertion,
    same_assertion,
)
from pdrd_analysis_service.domain.engineering_claims import REVIEW_REASON_CODES
from pdrd_analysis_service.domain.project_dependencies import (
    explicit_project_dependencies,
)
from pdrd_analysis_service.domain.signal_connections import build_signal_graph

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """Данные одной физической страницы после VLM understanding."""

    page: int
    text: str
    summary: str
    facts: tuple[DocumentFact, ...]
    text_words: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentContextOptions:
    """Границы выдачи D-sources и проверки документа."""

    max_related_facts_per_page: int
    max_text_sources_per_page: int
    semantic_threshold: float
    max_cross_page_candidates: int
    max_evidence_sources_per_finding: int
    validation_batch_size: int
    validation_num_predict: int
    max_saved_evidence_sources_per_finding: int = 2400


def _located(pages: tuple[DocumentPage, ...]) -> tuple[LocatedDocumentFact, ...]:
    """Присваивает фактам физические provenance ID."""
    return tuple(
        LocatedDocumentFact(page=page.page, index=index, fact=fact)
        for page in pages
        for index, fact in enumerate(page.facts, start=1)
    )


def document_engineering_report(pages, document_id):
    """Строит единый программный протокол документа после Understanding, без изменения prompt."""
    facts = tuple(
        LocatedDocumentFact(page.page, index, fact)
        for page in pages
        for index, fact in enumerate(
            (*page.facts, *explicit_project_dependencies(page.text, page.page)), 1
        )
    )
    registry = build_decision_registry(
        document_id=document_id,
        facts=facts,
        page_texts={page.page: page.text for page in pages},
    )
    return run_engineering_program(
        registry, page_texts={page.page: page.text for page in pages}
    )


def audit_document_findings(page_findings, report):
    """Применяет полные табличные контрдоказательства к локальным кандидатам."""
    return {
        page: attach_decision_protocols(
            audit_engineering_candidates(findings, report),
            report.registry,
            checks=report.checks,
        )
        for page, findings in page_findings.items()
    }


def build_page_document_context(
    *,
    pages: tuple[DocumentPage, ...],
    semantic_by_page: dict[int, tuple[dict[str, Any], ...]],
    options: DocumentContextOptions,
) -> dict[int, tuple[DocumentContextSource, ...]]:
    """Соединяет соседние страницы, точные теги и bounded semantic retrieval."""
    by_number = {page.page: page for page in pages}
    by_subject: dict[str, list[LocatedDocumentFact]] = {}
    by_table: dict[str, list[LocatedDocumentFact]] = {}
    for fact in _located(pages):
        key = subject_key(fact.fact)
        if key:
            by_subject.setdefault(key, []).append(fact)
        table_key = normalize_label(fact.fact.table_id)
        if table_key:
            by_table.setdefault(table_key, []).append(fact)

    result: dict[int, tuple[DocumentContextSource, ...]] = {}
    for page in pages:
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for neighbor in (page.page - 1, page.page + 1):
            adjacent = by_number.get(neighbor)
            if adjacent is None:
                continue
            source_id = f"D-p{neighbor:04d}-adj"
            seen.add(source_id)
            sources.append(
                {
                    "source_id": source_id,
                    "page": neighbor,
                    "match_type": "adjacent",
                    "score": 1.0,
                    "fact_id": None,
                    "subject": "",
                    "property": "",
                    "scope": "",
                    "text": f"{adjacent.summary}\n"
                    + (
                        adjacent.text
                        if any(fact.table_id for fact in page.facts)
                        or re.search(r"таблиц|м[³3]/(?:сут|час)", page.text, re.I)
                        else adjacent.text[:600]
                    ),
                    "evidence_text": adjacent.text[:300],
                    "visual_regions": [],
                }
            )

        related: list[tuple[LocatedDocumentFact, str]] = []
        related_keys: set[tuple[str, str]] = set()
        for current in page.facts:
            key = subject_key(current)
            if key and ("subject", key) not in related_keys:
                related_keys.add(("subject", key))
                related.extend(
                    (item, "exact_identifier") for item in by_subject.get(key, [])
                )
            table_key = normalize_label(current.table_id)
            if table_key and ("table", table_key) not in related_keys:
                related_keys.add(("table", table_key))
                related.extend(
                    (item, "table_identifier") for item in by_table.get(table_key, [])
                )
        for fact, match_type in related:
            if fact.page == page.page:
                continue
            source_id = f"D-{fact.fact_id}"
            if source_id in seen:
                continue
            seen.add(source_id)
            sources.append(
                {
                    "source_id": source_id,
                    "page": fact.page,
                    "match_type": match_type,
                    "score": 1.0,
                    "fact_id": fact.fact_id,
                    "subject": fact.fact.subject_name,
                    "property": fact.fact.property_name,
                    "scope": "; ".join(
                        (
                            fact.fact.scope_system,
                            fact.fact.scope_location,
                            fact.fact.scope_segment,
                            fact.fact.scope_operating_mode,
                            fact.fact.scope_condition,
                        )
                    ),
                    "text": fact_search_text(fact),
                    "evidence_text": fact.fact.evidence_text,
                    "visual_regions": _evidence_region(fact, by_number),
                }
            )
            if sum(
                item["match_type"] in {"exact_identifier", "table_identifier"}
                for item in sources
            ) >= (options.max_related_facts_per_page):
                break

        text_count = 0
        for semantic in semantic_by_page.get(page.page, ()):
            source_page = semantic.get("page")
            score = float(semantic.get("score", 0.0) or 0.0)
            if source_page is None or int(source_page) == page.page:
                continue
            if score < options.semantic_threshold:
                continue
            chunk = semantic.get("chunk_index")
            if not isinstance(chunk, int) or isinstance(chunk, bool) or chunk < 0:
                continue
            source_id = f"D-p{int(source_page):04d}-c{chunk}"
            if source_id in seen:
                continue
            seen.add(source_id)
            sources.append(
                {
                    "source_id": source_id,
                    "page": int(source_page),
                    "match_type": "semantic",
                    "score": score,
                    "fact_id": None,
                    "chunk_index": int(chunk),
                    "subject": "",
                    "property": "",
                    "scope": "",
                    "text": str(semantic.get("text", ""))[:900],
                    "evidence_text": str(semantic.get("text", ""))[:300],
                    "visual_regions": [],
                }
            )
            text_count += 1
            if text_count >= options.max_text_sources_per_page:
                break
        result[page.page] = tuple(
            DocumentContextSource(
                **{
                    key: value
                    for key, value in source.items()
                    if key != "visual_regions"
                },
                visual_regions=tuple(
                    FindingVisualRegion(**region) for region in source["visual_regions"]
                ),
            )
            for source in sources
        )
        logger.info(
            "document_context_retrieval page=%s exact_fact_matches=%s "
            "table_matches=%s semantic_text_matches=%s adjacent_sources=%s",
            page.page,
            sum(item["match_type"] == "exact_identifier" for item in sources),
            sum(item["match_type"] == "table_identifier" for item in sources),
            text_count,
            sum(item["match_type"] == "adjacent" for item in sources),
        )
    return result


def _evidence_region(
    fact: LocatedDocumentFact,
    pages_by_number: dict[int, DocumentPage],
) -> list[dict[str, Any]]:
    """Уточняет область факта по точному PDF-слову, если VLM её не указала."""
    if fact.fact.visual_regions:
        return list(fact.fact.visual_regions)
    page = pages_by_number[fact.page]
    quote_tokens = re.findall(
        r"\w+", fact.fact.evidence_text.casefold().replace(",", ".")
    )
    word_tokens = [
        re.findall(r"\w+", str(word.get("text", "")).casefold().replace(",", "."))
        for word in page.text_words
    ]
    for start in range(len(page.text_words)):
        tokens = []
        selected = []
        for offset in range(
            start, min(len(page.text_words), start + len(quote_tokens) + 2)
        ):
            tokens.extend(word_tokens[offset])
            selected.append(page.text_words[offset])
            if tokens == quote_tokens and tokens:
                boxes = [word.get("bbox") for word in selected]
                if all(
                    isinstance(box, dict)
                    and all(
                        isinstance(box.get(key), (int, float))
                        for key in ("x_min", "y_min", "x_max", "y_max")
                    )
                    for box in boxes
                ):
                    region = {
                        key: int(
                            (min if key.endswith("min") else max)(
                                box[key] for box in boxes
                            )
                        )
                        for key in ("x_min", "y_min", "x_max", "y_max")
                    }
                    if (
                        0 <= region["x_min"] < region["x_max"] <= 1000
                        and 0 <= region["y_min"] < region["y_max"] <= 1000
                    ):
                        return [{**region, "confidence": 0.9}]
                break
            if tokens != quote_tokens[: len(tokens)]:
                break
    needle = fact.fact.value_raw.strip().casefold().replace(",", ".")
    if not needle:
        return []
    matching_words = [
        word
        for word in page.text_words
        if str(word.get("text", "")).strip().casefold().replace(",", ".") == needle
    ]
    if len(matching_words) != 1:
        return []
    for word in matching_words:
        token = str(word.get("text", "")).strip().casefold().replace(",", ".")
        if token != needle:
            continue
        box = word.get("bbox")
        if not isinstance(box, dict):
            continue
        try:
            result = {
                key: int(box[key]) for key in ("x_min", "y_min", "x_max", "y_max")
            }
        except (KeyError, TypeError, ValueError):
            continue
        if (
            0 <= result["x_min"] < result["x_max"] <= 1000
            and 0 <= result["y_min"] < result["y_max"] <= 1000
        ):
            return [{**result, "confidence": 0.9}]
    return []


def _representative_facts(
    group: ConflictGroup,
    limit: int,
) -> tuple[LocatedDocumentFact, ...]:
    """Сохраняет сначала доказательство каждого различного значения, затем других страниц."""
    if group.mismatch_type == "inventory_composition":
        seen = set()
        selected = []
        for fact in group.facts:
            identity = (fact.fact.kind, normalize_label(fact.fact.identifier))
            if identity not in seen:
                seen.add(identity)
                selected.append(fact)
        return tuple(selected[:limit])
    chosen: list[LocatedDocumentFact] = []
    seen_values: set[tuple[str, str] | None] = set()
    for fact in group.facts:
        value = normalized_value(fact.fact)
        if value not in seen_values:
            chosen.append(fact)
            seen_values.add(value)
            if len(chosen) >= limit:
                return tuple(chosen)
    seen_pages = {fact.page for fact in chosen}
    for fact in group.facts:
        if fact.page not in seen_pages:
            chosen.append(fact)
            seen_pages.add(fact.page)
            if len(chosen) >= limit:
                break
    return tuple(chosen)


def _localize_programmatic_findings(findings, pages):
    """Локализует только новые программные findings по прежнему поиску PDF-слов."""
    by_number = {page.page: page for page in pages}
    result = []
    for finding in findings:
        assertion = read_assertion(finding.origin_assertions)
        if assertion is None:
            result.append(finding)
            continue
        regions = {
            item.fact_id: tuple(
                FindingVisualRegion(**region)
                for region in _evidence_region(item, by_number)
            )
            for item in assertion.observations
        }
        sources = tuple(
            replace(
                source,
                visual_regions=regions.get(source.fact_id, source.visual_regions),
            )
            for source in finding.document_context_basis_sources
        )
        locations = tuple(
            {
                **location,
                "visual_regions": [
                    region.as_dict()
                    for region in regions.get(location.get("fact_id"), ())
                ],
            }
            for location in finding.evidence_locations
        )
        primary = tuple(
            region
            for item in assertion.observations
            if item.page == finding.page
            for region in regions[item.fact_id]
        )
        result.append(
            replace(
                finding,
                visual_regions=primary,
                document_context_basis_sources=sources,
                evidence_locations=locations,
            )
        )
    return tuple(result)


class CrossPageCancelled(Exception):
    """Проверка отменена между пакетами валидации."""


def _candidate_payload(index: int, group: ConflictGroup, limit: int) -> dict[str, Any]:
    """Ограничивает VLM prompt только одной сопоставимой группой."""
    return {
        "candidate_id": f"C{index}",
        "mismatch_type": group.mismatch_type,
        "subject": group.subject_key,
        "property": group.property_key,
        **(
            {
                "declared_count": group.facts[0].fact.value_raw,
                "observed_unique_count": len(
                    {
                        normalize_label(fact.fact.identifier)
                        for fact in group.facts
                        if fact.fact.relation == "member_of"
                    }
                ),
            }
            if group.mismatch_type == "inventory_composition"
            else {}
        ),
        "facts": [
            {
                "page": fact.page,
                "fact_id": fact.fact_id,
                "identifier": fact.fact.identifier,
                "subject_type": fact.fact.subject_type,
                "property": fact.fact.property_name,
                "relation": fact.fact.relation,
                "value": fact.fact.value_raw,
                "unit": fact.fact.unit_raw,
                "scope": {
                    "system": fact.fact.scope_system,
                    "location": fact.fact.scope_location,
                    "segment": fact.fact.scope_segment,
                    "operating_mode": fact.fact.scope_operating_mode,
                    "condition": fact.fact.scope_condition,
                },
                "evidence": fact.fact.evidence_text,
            }
            for fact in _representative_facts(group, limit)
        ],
    }


@dataclass(frozen=True, slots=True)
class CheckCrossPageConsistency:
    """Подтверждает только ограниченные группы конфликтов одним VLM batch."""

    vision_model: StructuredVisionModel
    options: DocumentContextOptions

    async def execute(
        self,
        *,
        pages: tuple[DocumentPage, ...],
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
        document_id: str = "current-document",
        engineering_report: EngineeringReport | None = None,
    ) -> tuple[FindingDraft, ...]:
        """Сохраняет подтверждённые группы и конкретные подозрения с обеими сторонами evidence."""
        if cancel_check is not None and await cancel_check():
            raise CrossPageCancelled
        facts = _located(pages)
        report = engineering_report or document_engineering_report(pages, document_id)
        registry = report.registry
        if registry.document_id != document_id:
            raise ValueError("Программный протокол относится к другому документу.")
        graph = build_signal_graph(registry)
        deterministic_findings = _localize_programmatic_findings(
            program_findings(report), pages
        )
        logger.info(
            "design_decision_registry document=%s decisions=%s facts=%s "
            "unassigned=%s connections=%s unresolved_connections=%s new_vlm_calls=0",
            document_id,
            len(registry.decisions),
            len(registry.evidence),
            len(registry.unassigned_evidence_ids),
            len(graph.connections),
            len(graph.unresolved_evidence_ids),
        )
        groups = discover_conflicts(
            facts,
            max_candidates=self.options.max_cross_page_candidates,
        )
        for index, group in enumerate(groups, 1):
            trace_candidate(
                "cross_page_hypothesis",
                f"C{index}",
                decision="candidate",
                reason=group.mismatch_type,
                facts=len(group.facts),
                pages=len({fact.page for fact in group.facts}),
            )
        if not groups:
            logger.info(
                "cross_page_candidates generated=0 confirmed=0 rejected=0 needs_review=0"
            )
            return attach_decision_protocols(
                deterministic_findings, registry, checks=report.checks
            )
        decisions: dict[str, str] = {}
        reasons: dict[str, str] = {}
        reason_codes: dict[str, str] = {}
        assertions = {
            f"C{index}": assertion_from_facts(group.facts, include_related=True)
            for index, group in enumerate(groups, 1)
        }
        for key, assertion in assertions.items():
            if (
                assertion is not None
                and deterministic_unit_mismatch(assertion)
                and all(
                    item.fact.extraction_method in {"pdf_text", "pdf_table"}
                    for item in assertion.observations
                )
            ):
                decisions[key] = "confirmed"
                reasons[key] = (
                    "Программно проверен пересчёт одной строки с учётом округления."
                )
                reason_codes[key] = "false_arithmetic"
        batch_size = self.options.validation_batch_size
        for start in range(0, len(groups), batch_size):
            if cancel_check is not None and await cancel_check():
                raise CrossPageCancelled
            subset = groups[start : start + batch_size]
            candidates = [
                _candidate_payload(
                    start + index + 1,
                    group,
                    self.options.max_evidence_sources_per_finding,
                )
                for index, group in enumerate(subset)
                if f"C{start + index + 1}" not in decisions
            ]
            if not candidates:
                continue
            prompt = (
                "Проверь только перечисленные кандидаты внутренних противоречий "
                "ОДНОГО PDF. Сравни объект, свойство, режим, участок, условия и "
                "смысл значения. Разные режимы и объяснённые различия отклони. "
                "Не устанавливай, какое значение правильно. D не является нормативом. "
                "Верни решение для каждого candidate_id.\n"
                "Для unit_equivalence проверь пересчёт единиц и округление только в одной строке и режиме. "
                "Не дели суточное значение на 24 для периодического процесса. "
                "Название карты ОСР, DN/наружный диаметр, разные условия прокладки и разные "
                "области учёта не являются противоречием.\n"
                "Для inventory_composition сопоставь заявленное количество с уникальными тегами "
                "явных member_of той же группы и условий; существующие/проектируемые элементы не смешивай. "
                "Для sheet_sequence проверь печатные номера смежных страниц одного шифра, "
                "учитывая раздельные части и продолжение одного большого чертежа. "
                "Физический индекс PDF не является проектным номером листа.\n"
                + json.dumps(candidates, ensure_ascii=False)
            )
            schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "decisions": {
                        "type": "array",
                        "maxItems": len(candidates),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "candidate_id": {
                                    "type": "string",
                                    "enum": [
                                        item["candidate_id"] for item in candidates
                                    ],
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["confirmed", "rejected", "needs_review"],
                                },
                                "reason": {"type": "string", "maxLength": 300},
                                "reason_code": {
                                    "type": "string",
                                    "enum": list(REVIEW_REASON_CODES),
                                },
                            },
                            "required": [
                                "candidate_id",
                                "status",
                                "reason",
                                "reason_code",
                            ],
                        },
                    },
                },
                "required": ["decisions"],
            }
            generated = await self.vision_model.generate_json(
                prompt=prompt,
                schema=schema,
                num_predict=self.options.validation_num_predict,
                seed=700 + start,
                stage="cross_page_consistency",
            )
            for decision in generated.payload.get("decisions", []):
                if isinstance(decision, dict):
                    decisions[str(decision.get("candidate_id", ""))] = str(
                        decision.get("status", "")
                    )
                    reasons[str(decision.get("candidate_id", ""))] = str(
                        decision.get("reason", "")
                    ).strip()
                    code = str(decision.get("reason_code", ""))
                    if code in REVIEW_REASON_CODES:
                        reason_codes[str(decision.get("candidate_id", ""))] = code

        by_number = {page.page: page for page in pages}
        findings: list[FindingDraft] = []
        for index, group in enumerate(groups, start=1):
            decision = decisions.get(f"C{index}")
            trace_candidate(
                "cross_page_verification",
                f"C{index}",
                decision=decision
                if decision in {"confirmed", "needs_review", "rejected"}
                else "reject",
                reason=reason_codes.get(f"C{index}", "model_decision")
                if decision
                else "missing_decision",
            )
            if decision not in {"confirmed", "needs_review"}:
                continue
            if decision == "needs_review" and not reasons.get(f"C{index}"):
                continue
            selected = group.facts[
                : self.options.max_saved_evidence_sources_per_finding
            ]
            evidence_locations = tuple(
                {
                    "source_id": f"D-{fact.fact_id}",
                    "page": fact.page,
                    "fact_id": fact.fact_id,
                    "text": fact.fact.evidence_text,
                    "value_raw": fact.fact.value_raw,
                    "unit_raw": fact.fact.unit_raw,
                    "visual_regions": _evidence_region(fact, by_number),
                }
                for fact in selected
            )
            locations = "; ".join(
                f"стр. {fact.page}: {fact.fact.value_raw} {fact.fact.unit_raw}".strip()
                for fact in _representative_facts(
                    group, self.options.max_evidence_sources_per_finding
                )
            )
            first = selected[0]
            assertion = assertions[f"C{index}"]
            if assertion is not None:
                deterministic = reason_codes.get(f"C{index}") == "false_arithmetic"
                assertion = replace(
                    assertion,
                    verification="deterministic" if deterministic else "semantic",
                    verification_rule="unit_equivalence"
                    if deterministic
                    else "cross_page_comparability",
                )
            subject = first.fact.subject_name or first.fact.identifier
            prop = first.fact.property_name
            description = (
                "В документации обнаружено противоречие"
                if decision == "confirmed"
                else "Требует проверки возможное расхождение"
            )
            review_reason = (
                f" Причина проверки: {reasons[f'C{index}']}"
                if decision == "needs_review"
                else ""
            )
            comment = (
                f"{description} для {subject} по параметру «{prop}»: {locations}. "
                "Требуется определить корректное значение и привести документацию к единому значению."
                + review_reason
            )
            recommendation = "Сопоставить исходные данные и привести значения на указанных страницах к единому решению."
            if group.mismatch_type == "inventory_composition":
                tags = tuple(
                    dict.fromkeys(
                        fact.fact.identifier
                        for fact in group.facts
                        if fact.fact.relation == "member_of"
                    )
                )
                comment = (
                    f"{description} состава группы {first.fact.identifier}: заявлено {first.fact.value_raw}, "
                    f"в связанных материалах указаны {len(tags)} уникальных элементов: {', '.join(tags)}. "
                    "Требуется сверить состав и условия учёта элементов."
                    + review_reason
                )
                recommendation = (
                    "Сверить заявленное количество с перечнем той же группы, "
                    "исключив смешение существующих и проектируемых элементов."
                )
            elif group.mismatch_type == "sheet_sequence":
                comment = (
                    f"{description} в последовательности печатных номеров документа {first.fact.identifier}: "
                    f"{locations}. Сверить порядок листов, раздельные части и продолжения чертежа."
                    + review_reason
                )
                recommendation = (
                    "Проверить печатную нумерацию по перечню листов "
                    "и при подтверждении скорректировать последовательность."
                )
            findings.append(
                FindingDraft(
                    finding_id=f"cross-page-{index:04d}",
                    page=first.page,
                    page_type="document",
                    category="document_consistency",
                    severity="warning",
                    status="confirmed" if decision == "confirmed" else "needs_review",
                    comment=comment,
                    evidence=locations,
                    recommendation_draft=recommendation,
                    confidence=0.85 if decision == "confirmed" else 0.65,
                    normative_source_ids=(),
                    basis="",
                    basis_sources=(),
                    experience_query="",
                    visual_regions=(),
                    evidence_locations=evidence_locations,
                    document_context_source_ids=tuple(
                        item["source_id"] for item in evidence_locations
                    ),
                    document_context_basis_sources=tuple(
                        DocumentContextSource(
                            source_id=f"D-{fact.fact_id}",
                            page=fact.page,
                            fact_id=fact.fact_id,
                            text=fact_search_text(fact),
                            evidence_text=fact.fact.evidence_text,
                            score=1.0,
                            match_type="conflict",
                            subject=fact.fact.subject_name,
                            property=fact.fact.property_name,
                            scope="; ".join(
                                (
                                    fact.fact.scope_system,
                                    fact.fact.scope_location,
                                    fact.fact.scope_segment,
                                    fact.fact.scope_operating_mode,
                                    fact.fact.scope_condition,
                                )
                            ),
                            visual_regions=tuple(
                                FindingVisualRegion(**region)
                                for region in _evidence_region(fact, by_number)
                            ),
                        )
                        for fact in selected
                    ),
                    object_ref=first.fact.identifier,
                    origin_assertions=(
                        {
                            "origin": "cross_page_validation",
                            "candidate_id": f"C{index}",
                            "reason_code": reason_codes.get(
                                f"C{index}", "model_decision"
                            ),
                            "comparability_checked": True,
                        },
                        *((assertion.as_origin(),) if assertion else ()),
                    ),
                )
            )
        logger.info(
            "cross_page_candidates generated=%s confirmed=%s rejected=%s needs_review=%s",
            len(groups),
            sum(finding.status == "confirmed" for finding in findings),
            sum(value == "rejected" for value in decisions.values()),
            sum(value == "needs_review" for value in decisions.values()),
        )
        by_id = {
            finding.finding_id: finding
            for finding in (*findings, *deterministic_findings)
        }
        return attach_decision_protocols(
            tuple(by_id.values()), registry, checks=report.checks
        )


def merge_cross_page_findings(
    *,
    page_findings: dict[int, tuple[FindingDraft, ...]],
    cross_findings: tuple[FindingDraft, ...],
) -> dict[int, tuple[FindingDraft, ...]]:
    """Объединяет повтор одного доказанного противоречия, сохраняя все типы оснований."""
    structured = tuple(
        finding
        for findings in page_findings.values()
        for finding in findings
        if read_assertion(finding.origin_assertions) is not None
    )
    result = {
        page: [
            finding
            for finding in findings
            if read_assertion(finding.origin_assertions) is None
        ]
        for page, findings in page_findings.items()
    }
    for cross in (*structured, *cross_findings):
        source_ids = set(cross.document_context_source_ids)
        pages = {source.page for source in cross.document_context_basis_sources}
        duplicates = []
        for findings in result.values():
            for finding in tuple(findings):
                selected = set(finding.document_context_source_ids)
                left = read_assertion(finding.origin_assertions)
                right = read_assertion(cross.origin_assertions)
                structured_duplicate = (
                    left is not None
                    and right is not None
                    and same_assertion(left, right)
                )
                if structured_duplicate or (
                    finding.category == "document_consistency"
                    and finding.page in pages
                    and selected
                    and selected <= source_ids
                    and normalize_label(finding.object_ref)
                    == normalize_label(cross.object_ref)
                    and normalize_label(finding.comment)
                    == normalize_label(cross.comment)
                    and normalize_label(finding.evidence)
                    == normalize_label(cross.evidence)
                ):
                    duplicates.append(finding)
                    findings.remove(finding)
        updates = {}
        for field, id_field in (
            ("basis_sources", "normative_source_ids"),
            ("technical_assignment_basis_sources", "technical_assignment_source_ids"),
            ("user_package_basis_sources", "user_package_source_ids"),
            ("document_context_basis_sources", "document_context_source_ids"),
            (
                "equipment_documentation_basis_sources",
                "equipment_documentation_source_ids",
            ),
        ):
            sources = list(getattr(cross, field))
            for finding in duplicates:
                for source in getattr(finding, field):
                    if source in sources:
                        continue
                    existing = next(
                        (
                            item
                            for item in sources
                            if item.source_id == source.source_id
                        ),
                        None,
                    )
                    if (
                        field == "document_context_basis_sources"
                        and existing is not None
                        and all(
                            getattr(existing, name) == getattr(source, name)
                            for name in (
                                "page",
                                "fact_id",
                                "evidence_text",
                                "text",
                                "subject",
                                "property",
                                "scope",
                            )
                        )
                    ):
                        regions = list(existing.visual_regions)
                        regions.extend(
                            region
                            for region in source.visual_regions
                            if region not in regions
                        )
                        sources[sources.index(existing)] = replace(
                            existing,
                            visual_regions=tuple(regions),
                            score=max(existing.score, source.score),
                        )
                        continue
                    # N/T/U ID могут быть локальны для страницы; устраняем только коллизию ID.
                    if any(item.source_id == source.source_id for item in sources):
                        source = replace(
                            source,
                            source_id=f"{source.source_id}-p{finding.page}-m{len(sources)}",
                        )
                    sources.append(source)
            updates[field] = tuple(sources)
            updates[id_field] = tuple(source.source_id for source in sources)
        origins = (
            *cross.origin_assertions,
            *(origin for finding in duplicates for origin in finding.origin_assertions),
            *(
                {
                    "origin": "merged_finding",
                    "finding_id": item.finding_id,
                    "page": item.page,
                    "comment": item.comment,
                    "evidence": item.evidence,
                    "recommendation_draft": item.recommendation_draft,
                    "equipment_details": item.equipment_details,
                }
                for item in duplicates
            ),
        )
        evidence = []
        for item in (cross, *duplicates):
            locations = list(item.evidence_locations)
            if item.visual_regions:
                locations.append(
                    {
                        "page": item.page,
                        "source_id": item.finding_id,
                        "text": item.evidence,
                        "visual_regions": [
                            region.as_dict() for region in item.visual_regions
                        ],
                    }
                )
            for location in locations:
                if location not in evidence:
                    evidence.append(location)
        merged = replace(
            cross,
            **updates,
            origin_assertions=origins,
            evidence_locations=tuple(evidence),
            basis="\n".join(
                dict.fromkeys(item.basis for item in (cross, *duplicates) if item.basis)
            ),
        )
        assertion = read_assertion(cross.origin_assertions)
        for duplicate in duplicates:
            other = read_assertion(duplicate.origin_assertions)
            if (
                assertion is not None
                and other is not None
                and same_assertion(assertion, other)
            ):
                assertion = merge_assertions(assertion, other)
        if assertion is not None:
            merged = replace(
                merged,
                origin_assertions=(
                    assertion.as_origin(),
                    *(
                        origin
                        for origin in origins
                        if origin.get("origin") != "engineering_assertion"
                    ),
                    *(
                        {**origin, "origin": "engineering_assertion_history"}
                        for origin in origins
                        if origin.get("origin") == "engineering_assertion"
                        and origin != assertion.as_origin()
                    ),
                ),
            )
        result.setdefault(cross.page, []).append(merged)
    return {page: tuple(findings) for page, findings in result.items()}
