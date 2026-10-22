# services/analysis-service/src/pdrd_analysis_service/application/independent_normative.py

"""Независимый проход от полного атомарного корпуса к фактам всего документа."""

import asyncio
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    requirement_evidence,
    run_engineering_program,
)
from pdrd_analysis_service.application.ports.normative_requirements import (
    NormativeRequirementsReader,
    NormativeRequirementsUnavailable,
)
from pdrd_analysis_service.application.use_cases.document_context import DocumentPage
from pdrd_analysis_service.domain.analysis import NormativeSource
from pdrd_analysis_service.domain.design_decisions import build_decision_registry
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    normalize_label,
)
from pdrd_analysis_service.domain.engineering_facts import merge_text_engineering_facts
from pdrd_analysis_service.domain.evidence_audit import bind_evidence, literal_contains
from pdrd_analysis_service.domain.physical_identity import valid_box
from pdrd_analysis_service.domain.project_dependencies import (
    explicit_project_dependencies,
)


@dataclass(frozen=True, slots=True)
class IndependentNormativeResult:
    """Находки, неизменяемые версии источников и судьба каждого выбранного требования."""

    findings: tuple
    diagnostics: dict


@dataclass(frozen=True, slots=True)
class IndependentNormativeReview:
    """Одна программная проверка корпуса на задание, без дополнительных VLM/embedding."""

    reader: NormativeRequirementsReader

    async def execute(
        self,
        *,
        document_id: str,
        section_id: str,
        document_ids: tuple[str, ...],
        pages: tuple[DocumentPage, ...],
        generation_ids: dict[str, str] | None = None,
        assessed_on: str | None = None,
        check_cancellation: Callable[[], Awaitable[None]],
    ) -> IndependentNormativeResult:
        """Список предварительных findings намеренно не входит во входной контракт."""
        day = assessed_on or datetime.now(UTC).date().isoformat()
        sources, manifests, unavailable, selection = [], [], [], []
        facts, texts = [], {}
        for page in pages:
            number, text = page.page, page.text
            if number in texts:
                raise ValueError(
                    "Повтор физической страницы в независимом нормативном проходе."
                )
            texts[number] = text
            merged = (
                *merge_text_engineering_facts(page.facts, text, number),
                *explicit_project_dependencies(text, number),
            )
            facts.extend(
                LocatedDocumentFact(number, index, fact)
                for index, fact in enumerate(merged, 1)
            )
        project = normalize_label("\n".join(texts.values()))
        observed = []
        for item in facts:
            fact = item.fact
            binding = bind_evidence(item, texts)
            visual = (
                binding.reason_code == "visual_evidence_not_independently_verified"
                and fact.extraction_method not in {"pdf_text", "pdf_table", ""}
                and any(valid_box(region) for region in fact.visual_regions)
                and literal_contains(fact.evidence_text, fact.identifier)
                and (not fact.source_pages or item.page in fact.source_pages)
            )
            if binding.status == "supported" or visual:
                observed.append(
                    (
                        item.fact_id,
                        normalize_label(
                            " ".join(
                                (
                                    fact.subject_type,
                                    fact.subject_name,
                                    fact.object_kind,
                                    fact.evidence_text,
                                )
                            )
                        ),
                    )
                )
        for normative_id in sorted(set(document_ids)):
            await check_cancellation()
            try:
                document = await self.reader.load(
                    section_id=section_id,
                    document_id=normative_id,
                    generation_id=(generation_ids or {}).get(normative_id),
                    assessed_on=day,
                    check_cancellation=check_cancellation,
                )
            except NormativeRequirementsUnavailable as error:
                unavailable.append(
                    {
                        "document_id": normative_id,
                        "result": "source_missing",
                        "reason": str(error),
                    }
                )
                continue
            manifests.append(document.manifest)
            for row in document.requirements:
                # Отсутствие признака объекта не доказывает неприменимость общей нормы.
                subject = normalize_label(row.get("object_type", ""))
                families = (
                    "гидрант",
                    "резервуар",
                    "насос",
                    "труб",
                    "котельн",
                    "здани",
                    "сооруж",
                    "склад",
                    "фланц",
                    "грунт",
                )
                family = next((word for word in families if word in subject), None)
                anchors = [
                    fact_id for fact_id, text in observed if family and family in text
                ]
                relevant = family is None or family in project or bool(anchors)
                reason = (
                    "project_object_class_observed"
                    if family and relevant
                    else "applicability_requires_check"
                    if relevant
                    else "object_class_not_observed"
                )
                selection.append(
                    {
                        "requirement_id": row["requirement_id"],
                        "document_id": normative_id,
                        "generation_id": document.manifest["generation_id"],
                        "selected": relevant,
                        "reason": reason,
                        "observed_fact_ids": anchors,
                    }
                )
                if not relevant:
                    continue
                clause = row["clause"] + (
                    "." + row["subclause"] if row.get("subclause") else ""
                )
                text = row["text"]
                if clause and not re.match(
                    r"\s*" + re.escape(clause) + r"(?:\s|\.)", text
                ):
                    text = clause + " " + text
                sources.append(
                    NormativeSource(
                        source_id=row["requirement_id"],
                        point_id=row["point_id"],
                        score=1.0,
                        source_file=row["source_file"],
                        source_path=None,
                        page=row["page"],
                        chunk_index=None,
                        text=text,
                        document_id=normative_id,
                        section_id=section_id,
                        source_sha256=row["source_sha256"],
                        atomic_requirement=row,
                    )
                )
        await check_cancellation()

        def evaluate():
            """Считает полный корпус вне ASGI event loop, сохраняя контекст задания."""
            registry = build_decision_registry(
                document_id=document_id,
                facts=tuple(facts),
                page_texts=texts,
                document_context_enabled=True,
            )
            report = run_engineering_program(
                registry,
                page_texts=texts,
                requirement_sources=requirement_evidence(normative=tuple(sources)),
                requirement_plan_enabled=True,
                assessed_on=day,
            )
            report = replace(
                report,
                checks=tuple(
                    check
                    for check in report.checks
                    if check.rule.startswith("requirement_plan")
                ),
            )
            findings = program_findings(report, normative=tuple(sources))
            return report, findings

        report, findings = await asyncio.to_thread(evaluate)
        await check_cancellation()
        diagnostics = {
            **(report.requirement_plan or {}),
            "mode": "independent_atomic_corpus",
            "assessed_on": day,
            "generations": manifests,
            "unavailable_documents": unavailable,
            "selection": selection,
            "selection_counts": dict(Counter(row["reason"] for row in selection)),
            "corpus_received": True,
            "corpus_state": "unavailable"
            if unavailable
            else "ready"
            if manifests
            else "empty_selection",
            "active_generation_ids": [row["generation_id"] for row in manifests],
            "selected_atomic_statuses": dict(
                Counter(
                    source.atomic_requirement.get("extraction_status", "unknown")
                    for source in sources
                )
            ),
            "interpretation_counts": dict(
                Counter(
                    "not_supported"
                    if row.get("reason_code")
                    in {
                        "atomic_interpretation_requires_review",
                        "unsupported_requirement_rule",
                    }
                    else "interpreted"
                    for row in (report.requirement_plan or {}).get("checks", [])
                )
            ),
            "finding_statuses": dict(Counter(finding.status for finding in findings)),
            "confirmed_candidates_to_finalization": sum(
                finding.status == "confirmed" for finding in findings
            ),
            "proof_state": "edition_or_binding_unverified"
            if not (report.requirement_plan or {})
            .get("coverage", {})
            .get("currency", {})
            .get("current_mandatory", 0)
            else "evaluated_with_limits",
            "new_vlm_calls": 0,
            "new_embedding_calls": 0,
        }
        diagnostics["coverage"] = {
            **diagnostics.get("coverage", {}),
            "scope": "selected_atomic_generations",
            "corpus_requirements": len(selection),
            "source_complete": bool(manifests) and not unavailable,
            "selected_requirements": len(sources),
        }
        return IndependentNormativeResult(
            tuple(
                replace(finding, finding_id="n-first-" + finding.finding_id)
                for finding in findings
            ),
            diagnostics,
        )
