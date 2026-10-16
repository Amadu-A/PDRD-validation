# services/analysis-service/src/pdrd_analysis_service/application/final_evidence.py

"""Повторная проверка и объединение готовых текстов на тех же исходных фактах."""

from dataclasses import replace

from pdrd_analysis_service.application.evidence_audit import (
    diagnostic_record,
    finalize_evidence_candidate,
)
from pdrd_analysis_service.application.use_cases.document_context import (
    merge_cross_page_findings,
)
from pdrd_analysis_service.domain.analysis import FinalFinding, FindingDraft


def audit_final_findings(
    final_findings: tuple[FinalFinding, ...] | list[FinalFinding],
    drafts: tuple[FindingDraft, ...],
) -> tuple[tuple[FinalFinding, ...], tuple[dict[str, object], ...]]:
    """Проверяет фактический выход модели, сохраняя его отдельно от исправленного текста."""
    originals = {item.finding_id: item for item in drafts}
    finals = {item.finding_id: item for item in final_findings}
    checked, rejected = [], []
    for final in final_findings:
        original = originals[final.finding_id]
        output_trace = (
            (
                {
                    "origin": "finalization_output",
                    "finding_id": final.finding_id,
                    "comment": final.comment,
                    "recommendation": final.recommendation,
                    "evidence": final.evidence,
                    "status": final.status,
                },
            )
            if any(
                origin.get("origin")
                in {
                    "engineering_assertion",
                    "engineering_evidence_audit",
                    "engineering_evidence_context",
                    "finalization_model_output",
                }
                for origin in final.origin_assertions
            )
            else ()
        )
        draft = replace(
            original,
            comment=final.comment,
            evidence=final.evidence,
            recommendation_draft=final.recommendation,
            status=final.status,
            basis=final.basis,
            basis_sources=final.basis_sources,
            normative_source_ids=tuple(
                dict.fromkeys(
                    (
                        *original.normative_source_ids,
                        *(source.source_id for source in final.basis_sources),
                    )
                )
            ),
            technical_assignment_basis_sources=final.technical_assignment_basis_sources,
            user_package_basis_sources=final.user_package_basis_sources,
            document_context_source_ids=final.document_context_source_ids,
            document_context_basis_sources=final.document_context_basis_sources,
            equipment_documentation_source_ids=final.equipment_documentation_source_ids,
            equipment_documentation_basis_sources=final.equipment_documentation_basis_sources,
            origin_assertions=(*final.origin_assertions, *output_trace),
        )
        draft = finalize_evidence_candidate(draft)
        diagnostic = diagnostic_record(draft)
        if diagnostic is not None:
            rejected.append({**diagnostic, "stage": "post_finalization_evidence_audit"})
        else:
            checked.append(draft)
    by_page = {}
    for draft in checked:
        by_page.setdefault(draft.page, []).append(draft)
    merged = merge_cross_page_findings(
        page_findings={page: tuple(items) for page, items in by_page.items()},
        cross_findings=(),
    )
    result = []
    for items in merged.values():
        for draft in items:
            # Объединение может добавить наблюдения: повторяем проверку полного набора.
            draft = finalize_evidence_candidate(draft)
            diagnostic = diagnostic_record(draft)
            if diagnostic is not None:
                rejected.append({**diagnostic, "stage": "post_merge_evidence_audit"})
                continue
            ids = tuple(
                dict.fromkeys(
                    (
                        draft.finding_id,
                        *(
                            origin.get("finding_id")
                            for origin in draft.origin_assertions
                            if origin.get("origin") == "merged_finding"
                        ),
                    )
                )
            )
            experience = []
            for identifier in ids:
                for source in (
                    finals[identifier].experience_sources
                    if identifier in finals
                    else ()
                ):
                    if source not in experience:
                        experience.append(source)
            final = finals[draft.finding_id]
            result.append(
                replace(
                    final,
                    status=draft.status,
                    comment=draft.comment,
                    evidence=draft.evidence,
                    recommendation=draft.recommendation_draft,
                    basis=draft.basis,
                    basis_sources=draft.basis_sources,
                    experience_sources=tuple(experience),
                    technical_assignment_basis_sources=draft.technical_assignment_basis_sources,
                    user_package_basis_sources=draft.user_package_basis_sources,
                    document_context_source_ids=draft.document_context_source_ids,
                    document_context_basis_sources=draft.document_context_basis_sources,
                    equipment_documentation_source_ids=draft.equipment_documentation_source_ids,
                    equipment_documentation_basis_sources=draft.equipment_documentation_basis_sources,
                    evidence_locations=draft.evidence_locations,
                    origin_assertions=draft.origin_assertions,
                )
            )
    order = {item.finding_id: index for index, item in enumerate(final_findings)}
    return tuple(sorted(result, key=lambda item: order[item.finding_id])), tuple(
        rejected
    )
