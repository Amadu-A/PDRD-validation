# services/api-gateway/tests/unit/test_document_context_evidence.py

"""Регрессии доставки многостраничных доказательств в просмотр и PDF."""

from uuid import UUID

from pdrd_api_gateway.application.use_cases.get_analysis_annotated_pdf import (
    GetAnalysisAnnotatedPdf,
)
from pdrd_api_gateway.application.use_cases.get_analysis_visualization import (
    GetAnalysisVisualization,
)


def _finding() -> dict[str, object]:
    """Создаёт одно замечание с доказательствами на двух страницах."""
    return {
        "finding_id": "cross-page-0001",
        "page": 10,
        "status": "confirmed",
        "category": "document_consistency",
        "comment": "Разные значения температуры.",
        "evidence_locations": [
            {
                "page": 10,
                "text": "-37 °C",
                "visual_regions": [
                    {"x_min": 100, "y_min": 200, "x_max": 200, "y_max": 230}
                ],
            },
            {
                "page": 17,
                "text": "-35 °C",
                "visual_regions": [
                    {"x_min": 400, "y_min": 500, "x_max": 500, "y_max": 530}
                ],
            },
        ],
    }


def test_visualization_uses_page_specific_evidence() -> None:
    """Обе страницы получают одно замечание со своим текстом и областью."""
    findings = [_finding()]
    first = GetAnalysisVisualization._targets_for_page(
        findings=findings, page_number=10
    )
    second = GetAnalysisVisualization._targets_for_page(
        findings=findings, page_number=17
    )
    assert len(first) == len(second) == 1
    assert first[0].finding_id == second[0].finding_id == "cross-page-0001"
    assert first[0].evidence == "-37 °C"
    assert second[0].evidence == "-35 °C"
    assert len(first[0].visual_regions) == len(second[0].visual_regions) == 1


def test_pdf_export_marks_both_pages_with_one_report_finding() -> None:
    """PDF получает две аннотации, а отчёт сохраняет одно замечание."""
    annotations, report = GetAnalysisAnnotatedPdf._build_export_payload(
        job_id=UUID(int=1),
        result={"selected_pages": [10, 17], "findings": [_finding()]},
        visualization={
            "pages": [
                {
                    "page_number": page,
                    "locations": [
                        {
                            "finding_id": "cross-page-0001",
                            "status": "located",
                            "bbox": box,
                            "regions": [{"bbox": box}],
                        }
                    ],
                }
                for page, box in (
                    (10, {"x_min": 100, "y_min": 200, "x_max": 200, "y_max": 230}),
                    (17, {"x_min": 400, "y_min": 500, "x_max": 500, "y_max": 530}),
                )
            ]
        },
        source_mode="pdf_only",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
    )
    assert len(report.findings) == 1
    assert [item.page_number for item in annotations] == [10, 17]
    assert all(item.regions for item in annotations)

    assert {item.finding_id for item in annotations} == {"cross-page-0001"}
    assert {item.number for item in annotations} == {1}
    assert report.findings[0].title == "№1 · Страницы 10, 17"
