# services/api-gateway/tests/unit/test_analysis_visualization.py

"""Unit tests hybrid lazy visualization completed analysis."""

from uuid import UUID

import pytest
from pdrd_api_gateway.application.finding_anchor_matcher import (
    FindingAnchorMatcher,
)
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisFindingLocation,
    AnalysisFindingTarget,
    AnalysisPagePreview,
    AnalysisTextWord,
    AnalysisVisualRegion,
)
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisRequestArtifacts,
)
from pdrd_api_gateway.application.use_cases.get_analysis_visualization import (
    GetAnalysisVisualization,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSubmission,
)


class FakeGetJob:
    """Fake GetAnalysisJob."""

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
    """Минимальный artifact store."""

    def __init__(
        self,
        *,
        request: AnalysisRequestArtifacts,
        result: dict[
            str,
            object,
        ],
    ) -> None:
        """Сохраняет request/result."""
        self.request = request
        self.result = result

    async def load_request(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisRequestArtifacts:
        """Возвращает request."""
        assert document_id == self.request.submission.document_id

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
        assert document_id == self.request.submission.document_id

        return self.result


class FakeRenderer:
    """Fake Document Service renderer."""

    def __init__(
        self,
        pages: tuple[
            AnalysisPagePreview,
            ...,
        ],
    ) -> None:
        """Сохраняет pages."""
        self.pages = pages
        self.calls = 0
        self.page_spec: str | None = None

    async def render(
        self,
        *,
        pdf_content: bytes,
        file_name: str,
        page_spec: str | None,
    ) -> tuple[
        AnalysisPagePreview,
        ...,
    ]:
        """Возвращает synthetic pages."""
        assert pdf_content == b"pdf"

        assert file_name == "drawing.pdf"

        self.calls += 1

        self.page_spec = page_spec

        return self.pages


class FakeLocator:
    """Fake VLM fallback locator."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует counters."""
        self.calls = 0

        self.finding_counts: list[int] = []

    async def localize(
        self,
        *,
        page_number: int,
        extracted_text: str,
        image_base64: str,
        findings: tuple[
            AnalysisFindingTarget,
            ...,
        ],
    ) -> tuple[
        AnalysisFindingLocation,
        ...,
    ]:
        """Возвращает deterministic fake VLM bbox."""
        assert page_number >= 1
        assert extracted_text
        assert image_base64

        self.calls += 1

        self.finding_counts.append(
            len(
                findings,
            )
        )

        return tuple(
            AnalysisFindingLocation.located(
                finding_id=(finding.finding_id),
                regions=(
                    AnalysisVisualRegion(
                        bbox=(
                            AnalysisBoundingBox(
                                x_min=500,
                                y_min=500,
                                x_max=600,
                                y_max=600,
                            )
                        ),
                        source="vlm",
                        confidence=0.9,
                    ),
                ),
                confidence=0.9,
                method="vlm",
            )
            for finding in findings
        )


def completed_job(
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


def pdf_word(
    text: str,
) -> AnalysisTextWord:
    """Создаёт synthetic positioned PDF word."""
    return AnalysisTextWord(
        text=text,
        bbox=AnalysisBoundingBox(
            x_min=100,
            y_min=200,
            x_max=140,
            y_max=220,
        ),
        block_no=1,
        line_no=1,
        word_no=1,
    )


@pytest.mark.asyncio
async def test_exact_pdf_anchor_skips_vlm_localization() -> None:
    """Если XT1 есть в PDF text layer, VLM не нужен."""
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

    renderer = FakeRenderer(
        pages=(
            AnalysisPagePreview(
                page_number=14,
                width_points=841.89,
                height_points=595.28,
                image_base64=("iVBORw0KGgo="),
                extracted_text=("XT1"),
                text_words=(
                    pdf_word(
                        "XT1",
                    ),
                ),
            ),
        ),
    )

    locator = FakeLocator()

    job = completed_job(
        submission,
    )

    use_case = GetAnalysisVisualization(
        get_analysis_job=FakeGetJob(
            job,
        ),  # type: ignore[arg-type]
        artifact_store=FakeArtifactStore(
            request=request,
            result={
                "findings": [
                    {
                        "finding_id": ("F-1"),
                        "page": 14,
                        "comment": ("Нет маркировки XT1."),
                        "evidence": ("Разъём XT1 не маркирован."),
                    },
                ],
            },
        ),  # type: ignore[arg-type]
        pdf_page_renderer=(renderer),
        finding_locator=(locator),  # type: ignore[arg-type]
        anchor_matcher=(FindingAnchorMatcher()),
    )

    payload = await use_case.execute(
        job_id=job.id,
    )

    pages = payload["pages"]

    assert isinstance(
        pages,
        list,
    )

    location = pages[0]["locations"][0]

    assert location["status"] == "located"

    assert location["method"] == "pdf_text"

    assert (
        len(
            location["regions"],
        )
        == 1
    )

    assert locator.calls == 0


@pytest.mark.asyncio
async def test_unresolved_finding_uses_vlm_fallback() -> None:
    """Pure graphical finding уходит в один VLM fallback."""
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

    renderer = FakeRenderer(
        pages=(
            AnalysisPagePreview(
                page_number=14,
                width_points=841.89,
                height_points=595.28,
                image_base64=("iVBORw0KGgo="),
                extracted_text=("synthetic sheet"),
                text_words=(),
            ),
        ),
    )

    locator = FakeLocator()

    job = completed_job(
        submission,
    )

    use_case = GetAnalysisVisualization(
        get_analysis_job=FakeGetJob(
            job,
        ),  # type: ignore[arg-type]
        artifact_store=FakeArtifactStore(
            request=request,
            result={
                "findings": [
                    {
                        "finding_id": ("F-1"),
                        "page": 14,
                        "comment": ("Некорректное соединение."),
                        "evidence": ("Провод подключён не к тому контакту."),
                    },
                ],
            },
        ),  # type: ignore[arg-type]
        pdf_page_renderer=(renderer),
        finding_locator=(locator),  # type: ignore[arg-type]
        anchor_matcher=(FindingAnchorMatcher()),
    )

    payload = await use_case.execute(
        job_id=job.id,
    )

    location = payload["pages"][0]["locations"][0]

    assert location["method"] == "vlm"

    assert locator.calls == 1


@pytest.mark.asyncio
async def test_cad_only_visualization_skips_pdf_and_locator() -> None:
    """CAD-only не создаёт фиктивный PDF preview."""
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

    renderer = FakeRenderer(
        pages=(),
    )

    locator = FakeLocator()

    job = completed_job(
        submission,
    )

    use_case = GetAnalysisVisualization(
        get_analysis_job=FakeGetJob(
            job,
        ),  # type: ignore[arg-type]
        artifact_store=FakeArtifactStore(
            request=request,
            result={
                "findings": [],
            },
        ),  # type: ignore[arg-type]
        pdf_page_renderer=(renderer),
        finding_locator=(locator),  # type: ignore[arg-type]
        anchor_matcher=(FindingAnchorMatcher()),
    )

    payload = await use_case.execute(
        job_id=job.id,
    )

    assert payload["pages"] == []

    assert renderer.calls == 0

    assert locator.calls == 0


@pytest.mark.asyncio
async def test_fifty_unresolved_findings_use_one_vlm_call_per_page() -> None:
    """50 unresolved findings всё равно дают только один VLM request."""
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

    renderer = FakeRenderer(
        pages=(
            AnalysisPagePreview(
                page_number=1,
                width_points=1000.0,
                height_points=700.0,
                image_base64=("iVBORw0KGgo="),
                extracted_text=("synthetic sheet"),
                text_words=(),
            ),
        ),
    )

    findings = [
        {
            "finding_id": (f"F-{index:02d}"),
            "page": 1,
            "comment": (f"Графическое замечание {index}"),
            "evidence": (f"Графический факт {index}"),
        }
        for index in range(
            50,
        )
    ]

    locator = FakeLocator()

    job = completed_job(
        submission,
    )

    use_case = GetAnalysisVisualization(
        get_analysis_job=FakeGetJob(
            job,
        ),  # type: ignore[arg-type]
        artifact_store=FakeArtifactStore(
            request=request,
            result={
                "findings": findings,
            },
        ),  # type: ignore[arg-type]
        pdf_page_renderer=(renderer),
        finding_locator=(locator),  # type: ignore[arg-type]
        anchor_matcher=(FindingAnchorMatcher()),
    )

    payload = await use_case.execute(
        job_id=job.id,
    )

    pages = payload["pages"]

    assert isinstance(
        pages,
        list,
    )

    assert (
        len(
            pages[0]["locations"],
        )
        == 50
    )

    assert renderer.calls == 1

    assert locator.calls == 1

    assert locator.finding_counts == [
        50,
    ]
