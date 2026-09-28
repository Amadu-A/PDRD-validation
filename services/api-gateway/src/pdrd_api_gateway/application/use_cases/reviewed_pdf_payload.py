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
        fields = (
            AnalysisPdfReportField("Замечание", row.text),
            AnalysisPdfReportField(
                "Нормативное основание", row.normative_basis or "Не указано."
            ),
            AnalysisPdfReportField("Лист", str(row.page_number)),
            AnalysisPdfReportField("Тег", row.experience_tag),
        )
        title = f"Замечание №{row.number}"
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
