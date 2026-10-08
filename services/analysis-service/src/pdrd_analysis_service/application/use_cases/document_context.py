# services/analysis-service/src/pdrd_analysis_service/application/use_cases/document_context.py

"""Ограниченный D-контекст страницы и проверка противоречий PDF."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from pdrd_analysis_service.application.ports.vision_model import StructuredVisionModel
from pdrd_analysis_service.domain.analysis import (
    DocumentContextSource,
    FindingDraft,
    FindingVisualRegion,
)
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
                    "text": f"{adjacent.summary}\n{adjacent.text[:600]}",
                    "evidence_text": adjacent.text[:300],
                    "visual_regions": [],
                }
            )

        related: list[tuple[LocatedDocumentFact, str]] = []
        for current in page.facts:
            key = subject_key(current)
            if key:
                related.extend(
                    (item, "exact_identifier") for item in by_subject.get(key, [])
                )
            table_key = normalize_label(current.table_id)
            if table_key:
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
    needle = fact.fact.value_raw.strip().casefold().replace(",", ".")
    if not needle:
        return []
    for word in page.text_words:
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


class CrossPageCancelled(Exception):
    """Проверка отменена между пакетами валидации."""


def _candidate_payload(index: int, group: ConflictGroup, limit: int) -> dict[str, Any]:
    """Ограничивает VLM prompt только одной сопоставимой группой."""
    return {
        "candidate_id": f"C{index}",
        "mismatch_type": group.mismatch_type,
        "subject": group.subject_key,
        "property": group.property_key,
        "facts": [
            {
                "page": fact.page,
                "fact_id": fact.fact_id,
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
    ) -> tuple[FindingDraft, ...]:
        """Создаёт по одному замечанию на подтверждённую группу."""
        groups = discover_conflicts(
            _located(pages),
            max_candidates=self.options.max_cross_page_candidates,
        )
        if not groups:
            logger.info(
                "cross_page_candidates generated=0 confirmed=0 rejected=0 needs_review=0"
            )
            return ()
        decisions: dict[str, str] = {}
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
            ]
            prompt = (
                "Проверь только перечисленные кандидаты внутренних противоречий "
                "ОДНОГО PDF. Сравни объект, свойство, режим, участок, условия и "
                "смысл значения. Разные режимы и объяснённые различия отклони. "
                "Не устанавливай, какое значение правильно. D не является нормативом. "
                "Верни решение для каждого candidate_id.\n"
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
                            },
                            "required": ["candidate_id", "status", "reason"],
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

        by_number = {page.page: page for page in pages}
        findings: list[FindingDraft] = []
        for index, group in enumerate(groups, start=1):
            if decisions.get(f"C{index}") != "confirmed":
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
            subject = first.fact.subject_name or first.fact.identifier
            prop = first.fact.property_name
            findings.append(
                FindingDraft(
                    finding_id=f"cross-page-{index:04d}",
                    page=first.page,
                    page_type="document",
                    category="document_consistency",
                    severity="warning",
                    status="confirmed",
                    comment=(
                        f"В документации обнаружено противоречие для {subject} "
                        f"по параметру «{prop}»: {locations}. "
                        "Требуется определить корректное значение и привести "
                        "документацию к единому значению."
                    ),
                    evidence=locations,
                    recommendation_draft=(
                        "Сопоставить исходные данные и привести значения "
                        "на указанных страницах к единому решению."
                    ),
                    confidence=0.85,
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
                )
            )
        logger.info(
            "cross_page_candidates generated=%s confirmed=%s rejected=%s needs_review=%s",
            len(groups),
            len(findings),
            sum(value == "rejected" for value in decisions.values()),
            sum(value == "needs_review" for value in decisions.values()),
        )
        return tuple(findings)


def merge_cross_page_findings(
    *,
    page_findings: dict[int, tuple[FindingDraft, ...]],
    cross_findings: tuple[FindingDraft, ...],
) -> dict[int, tuple[FindingDraft, ...]]:
    """Объединяет повтор одного доказанного противоречия, сохраняя все типы оснований."""
    result = {page: list(findings) for page, findings in page_findings.items()}
    for cross in cross_findings:
        source_ids = set(cross.document_context_source_ids)
        pages = {source.page for source in cross.document_context_basis_sources}
        duplicates = []
        for findings in result.values():
            for finding in tuple(findings):
                selected = set(finding.document_context_source_ids)
                if (
                    finding.category == "document_consistency"
                    and finding.page in pages
                    and selected
                    and selected <= source_ids
                ):
                    duplicates.append(finding)
                    findings.remove(finding)
        updates = {}
        for field, id_field in (
            ("basis_sources", "normative_source_ids"),
            ("technical_assignment_basis_sources", "technical_assignment_source_ids"),
            ("user_package_basis_sources", "user_package_source_ids"),
            ("document_context_basis_sources", "document_context_source_ids"),
        ):
            sources = list(getattr(cross, field))
            for finding in duplicates:
                for source in getattr(finding, field):
                    if source in sources or (
                        field == "document_context_basis_sources"
                        and any(item.source_id == source.source_id for item in sources)
                    ):
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
        )
        merged = replace(
            cross,
            **updates,
            origin_assertions=origins,
            basis="\n".join(
                dict.fromkeys(item.basis for item in (cross, *duplicates) if item.basis)
            ),
        )
        result.setdefault(cross.page, []).append(merged)
    return {page: tuple(findings) for page, findings in result.items()}
