# services/api-gateway/tests/unit/test_analysis_annotated_pdf.py

"""Unit tests downloadable annotated PDF use case."""

from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisPdfAnnotation,
    AnalysisPdfReport,
)
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.application.use_cases.get_analysis_annotated_pdf import (
    AnalysisAnnotatedPdfSourceUnavailableError,
    GetAnalysisAnnotatedPdf,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)


class FakeGetJob:
    """Возвращает один completed job."""

    def __init__(
        self,
        job: AnalysisJob,
    ) -> None:
        """Сохраняет job."""
        self.job = job

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает job."""
        assert job_id == self.job.id

        return self.job


class FakeArtifactStore:
    """Возвращает source request и result."""

    def __init__(
        self,
        *,
        request: AnalysisRequestArtifacts,
        result: dict[
            str,
            object,
        ],
    ) -> None:
        """Сохраняет artifacts."""
        self.request = request
        self.result = result

    async def load_request(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisRequestArtifacts:
        """Возвращает request."""
        assert document_id == (self.request.submission.document_id)

        return self.request

    async def load_result(
        self,
        *,
        document_id: UUID,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает result."""
        assert document_id == (self.request.submission.document_id)

        return self.result


class FakeVisualization:
    """Возвращает server-owned finding locations."""

    def __init__(
        self,
        payload: dict[
            str,
            object,
        ],
    ) -> None:
        """Сохраняет payload."""
        self.payload = payload
        self.calls = 0

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает visualization."""
        del job_id

        self.calls += 1

        return self.payload


class FakeRenderer:
    """Фиксирует Document Service export request."""

    def __init__(
        self,
    ) -> None:
        """Создаёт empty state."""
        self.calls = 0

        self.annotations: tuple[
            AnalysisPdfAnnotation,
            ...,
        ] = ()

        self.report: AnalysisPdfReport | None = None

    async def render(
        self,
        *,
        pdf_content: bytes,
        file_name: str,
        annotations: tuple[
            AnalysisPdfAnnotation,
            ...,
        ],
        report: AnalysisPdfReport,
    ) -> bytes:
        """Возвращает synthetic PDF."""
        assert pdf_content == b"pdf"

        assert file_name == "drawing.pdf"

        self.calls += 1
        self.annotations = annotations
        self.report = report

        return b"%PDF-1.7\nannotated"


class FakeCache:
    """Хранит final annotated PDF."""

    def __init__(
        self,
        annotated_pdf: (bytes | None) = None,
    ) -> None:
        """Сохраняет initial cache."""
        self.annotated_pdf = annotated_pdf

        self.save_calls = 0

    async def load_annotated_pdf(
        self,
        *,
        document_id: UUID,
    ) -> bytes | None:
        """Возвращает final cache."""
        del document_id

        return self.annotated_pdf

    async def save_annotated_pdf(
        self,
        *,
        document_id: UUID,
        content: bytes,
    ) -> None:
        """Сохраняет final cache."""
        del document_id

        self.save_calls += 1

        self.annotated_pdf = content


def _completed_job(
    submission: AnalysisSubmission,
) -> AnalysisJob:
    """Создаёт completed job."""
    job = AnalysisJob.create(
        document_id=(submission.document_id),
    )

    job.mark_queued()

    job.mark_processing()

    job.mark_completed()

    return job


def _finding_field_map(
    report: AnalysisPdfReport,
    index: int,
) -> dict[
    str,
    str,
]:
    """Индексирует report fields."""
    return {field.label: field.value for field in report.findings[index].fields}


@pytest.mark.asyncio
async def test_export_keeps_all_findings_and_marks_unlocated_on_page() -> None:
    """Unlocated finding получает page-level annotation без fake bbox."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="14",
        pdf_file_name=("drawing.pdf"),
        cad_file_name=None,
    )

    request = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=b"pdf",
        cad_content=None,
    )

    result = {
        "status": "completed",
        "source_mode": "pdf_only",
        "selected_pages": [
            14,
        ],
        "findings_count": 2,
        "summary": ("Есть замечания."),
        "findings": [
            {
                "finding_id": "F-1",
                "page": 14,
                "status": ("needs_review"),
                "severity": "error",
                "category": "marking",
                "comment": ("Нет маркировки XT1."),
                "evidence": ("XT1 не подписан."),
                "basis": ("Требование нормы."),
                "normative_sources": [
                    {
                        "source_file": ("СП.pdf"),
                        "page": 3,
                        "point_id": ("5.1"),
                    },
                ],
                ("technical_assignment_basis_sources"): [
                    {
                        "source_file": ("ТЗ.pdf"),
                        "page": 4,
                    },
                ],
                ("user_package_basis_sources"): [
                    {
                        "source_file": ("Исходные.pdf"),
                        "page": 2,
                    },
                ],
                "recommendation": ("Добавить маркировку."),
            },
            {
                "finding_id": "F-2",
                "page": 14,
                "status": ("needs_review"),
                "severity": ("warning"),
                "category": "other",
                "comment": ("Второе замечание."),
                "recommendation": ("Проверить вручную."),
            },
        ],
        "limitations": [],
    }

    visualization = FakeVisualization(
        {
            "pages": [
                {
                    "page_number": 14,
                    "locations": [
                        {
                            "finding_id": ("F-1"),
                            "status": ("located"),
                            "bbox": {
                                "x_min": 100,
                                "y_min": 200,
                                "x_max": 300,
                                "y_max": 400,
                            },
                            "regions": [
                                {
                                    "bbox": {
                                        "x_min": 100,
                                        "y_min": 200,
                                        "x_max": 300,
                                        "y_max": 400,
                                    },
                                },
                            ],
                        },
                        {
                            "finding_id": ("F-2"),
                            "status": ("unlocated"),
                            "regions": [],
                        },
                    ],
                },
            ],
        }
    )

    renderer = FakeRenderer()

    cache = FakeCache()

    job = _completed_job(
        submission,
    )

    use_case = GetAnalysisAnnotatedPdf(
        get_analysis_job=(
            FakeGetJob(
                job,
            )
        ),  # type: ignore[arg-type]
        artifact_store=(
            FakeArtifactStore(
                request=request,
                result=result,
            )
        ),  # type: ignore[arg-type]
        get_analysis_visualization=(visualization),  # type: ignore[arg-type]
        renderer=(renderer),  # type: ignore[arg-type]
        visualization_cache=(cache),  # type: ignore[arg-type]
    )

    document = await use_case.execute(
        job_id=job.id,
    )

    assert document.file_name == "drawing_annotated.pdf"

    assert document.content.startswith(
        b"%PDF-",
    )

    assert renderer.calls == 1

    assert (
        len(
            renderer.annotations,
        )
        == 2
    )

    first_annotation = renderer.annotations[0]

    second_annotation = renderer.annotations[1]

    assert first_annotation.finding_id == "F-1"

    assert first_annotation.page_number == 14

    assert (
        len(
            first_annotation.regions,
        )
        == 1
    )

    assert second_annotation.finding_id == "F-2"

    assert second_annotation.page_number == 14

    assert second_annotation.regions == ()

    assert renderer.report is not None

    assert (
        len(
            renderer.report.findings,
        )
        == 2
    )

    first_fields = _finding_field_map(
        renderer.report,
        0,
    )

    second_fields = _finding_field_map(
        renderer.report,
        1,
    )

    assert "СП.pdf" in first_fields["Нормативные источники"]

    assert "ТЗ.pdf" in first_fields[("Требования технического задания")]

    assert "Исходные.pdf" in first_fields[("Пользовательские требования / документы")]

    assert first_fields["Локализация"] == (
        "PDF-аннотация размещена по найденной области на листе 14."
    )

    assert second_fields["Локализация"] == (
        "Точное место автоматически "
        "не локализовано. "
        "PDF-аннотация размещена "
        "на уровне листа 14."
    )

    assert cache.save_calls == 1


@pytest.mark.asyncio
async def test_final_pdf_cache_skips_visualization_and_renderer() -> None:
    """Повторное скачивание не запускает VLM/PDF generation."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="1",
        pdf_file_name=("drawing.pdf"),
        cad_file_name=None,
    )

    request = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=b"pdf",
        cad_content=None,
    )

    visualization = FakeVisualization(
        {
            "pages": [],
        }
    )

    renderer = FakeRenderer()

    cache = FakeCache(
        (b"%PDF-1.7\ncached"),
    )

    job = _completed_job(
        submission,
    )

    use_case = GetAnalysisAnnotatedPdf(
        get_analysis_job=(
            FakeGetJob(
                job,
            )
        ),  # type: ignore[arg-type]
        artifact_store=(
            FakeArtifactStore(
                request=request,
                result={
                    "status": ("completed"),
                    "findings": [],
                },
            )
        ),  # type: ignore[arg-type]
        get_analysis_visualization=(visualization),  # type: ignore[arg-type]
        renderer=(renderer),  # type: ignore[arg-type]
        visualization_cache=(cache),  # type: ignore[arg-type]
    )

    document = await use_case.execute(
        job_id=job.id,
    )

    assert document.content == b"%PDF-1.7\ncached"

    assert visualization.calls == 0

    assert renderer.calls == 0


@pytest.mark.asyncio
async def test_cad_only_job_has_no_annotated_pdf() -> None:
    """CAD-only не создаёт фиктивный PDF."""
    submission = AnalysisSubmission.create(
        pdf_present=False,
        cad_present=True,
        pages=None,
        pdf_file_name=None,
        cad_file_name=("drawing.dxf"),
    )

    request = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=None,
        cad_content=b"cad",
    )

    job = _completed_job(
        submission,
    )

    use_case = GetAnalysisAnnotatedPdf(
        get_analysis_job=(
            FakeGetJob(
                job,
            )
        ),  # type: ignore[arg-type]
        artifact_store=(
            FakeArtifactStore(
                request=request,
                result={
                    "status": ("completed"),
                    "findings": [],
                },
            )
        ),  # type: ignore[arg-type]
        get_analysis_visualization=(
            FakeVisualization(
                {
                    "pages": [],
                }
            )
        ),  # type: ignore[arg-type]
        renderer=(FakeRenderer()),  # type: ignore[arg-type]
        visualization_cache=(FakeCache()),  # type: ignore[arg-type]
    )

    with pytest.raises(
        AnalysisAnnotatedPdfSourceUnavailableError,
    ):
        await use_case.execute(
            job_id=job.id,
        )
