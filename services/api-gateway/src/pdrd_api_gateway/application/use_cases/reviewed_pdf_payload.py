# services/api-gateway/src/pdrd_api_gateway/application/use_cases/reviewed_pdf_payload.py

"""Чистое преобразование утверждённых замечаний в контракт Document Service."""

from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisPdfAnnotation,
    AnalysisPdfReport,
    AnalysisPdfReportField,
    AnalysisPdfReportFinding,
)
from pdrd_api_gateway.application.ports.reviewed_pdf import ReviewedPdfManifest


def reviewed_payload(manifest: ReviewedPdfManifest):
    """Сохраняет полный текст; не создаёт карточку для замечания без проверенной области."""
    annotations, findings = [], []
    for row in manifest.findings:
        pages = sorted(
            {row.page_number, *(location.page for location in row.evidence_locations)}
        )
        page_label = ("Страницы " if len(pages) > 1 else "Страница ") + ", ".join(
            map(str, pages)
        )
        fields = (
            AnalysisPdfReportField("Замечание", row.text),
            AnalysisPdfReportField(
                "Нормативное основание", row.normative_basis or "Не указано."
            ),
            AnalysisPdfReportField("Листы", ", ".join(map(str, pages))),
            AnalysisPdfReportField(
                "Источники",
                " ".join(f"[{kind}]" for kind in row.source_kinds) or "Инженерное",
            ),
            AnalysisPdfReportField(
                "Контекст проверяемого PDF",
                "\n".join(row.document_context_basis_sources) or "Не указано.",
            ),
            *(
                (
                    AnalysisPdfReportField(
                        "Документация производителя [EQ]",
                        "\n".join(row.equipment_documentation_basis_sources),
                    ),
                )
                if row.equipment_documentation_basis_sources
                else ()
            ),
            AnalysisPdfReportField("Тег", row.experience_tag),
        )
        title = f"№{row.number} · {page_label}"
        findings.append(
            AnalysisPdfReportFinding(title=title, fields=fields, origin=row.origin)
        )
        if row.regions:
            content = "\n\n".join(f"{field.label}: {field.value}" for field in fields)
            annotations.append(
                AnalysisPdfAnnotation(
                    number=row.number,
                    finding_id=row.finding_id,
                    page_number=row.page_number,
                    title=row.text,
                    content=content,
                    regions=row.regions,
                    callout_box=row.callout_box,
                    origin=row.origin,
                )
            )
        for location in row.evidence_locations:
            if location.page == row.page_number or not location.regions:
                continue
            annotations.append(
                AnalysisPdfAnnotation(
                    number=row.number,
                    finding_id=row.finding_id,
                    page_number=location.page,
                    title=row.text,
                    content="\n\n".join(
                        f"{field.label}: {field.value}" for field in fields
                    ),
                    regions=location.regions,
                    origin=row.origin,
                )
            )
    return tuple(annotations), AnalysisPdfReport(
        title="Итоговый PDF после Human Review",
        metadata=(
            AnalysisPdfReportField("Документ", manifest.source_filename),
            AnalysisPdfReportField("Утверждённая редакция", str(manifest.revision)),
        ),
        summary=f"Принято замечаний: {len(findings)}.",
        findings=tuple(findings),
        limitations=(
            "Замечания без подтверждённой области приведены только в текстовом списке.",
        ),
    )
