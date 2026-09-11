# services/api-gateway/tests/unit/test_analysis_visualization.py

"""Unit tests lazy PDF visualization endpoint use case."""

from typing import Any
from uuid import UUID

import pytest
from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisPagePreview,
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
        request: AnalysisRequestArtifacts,
    ) -> None:
        """Сохраняет request."""
        self.request = request

    async def load_request(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisRequestArtifacts:
        """Возвращает request."""
        assert document_id == self.request.submission.document_id
        return self.request


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


def completed_job(
    submission: AnalysisSubmission,
) -> AnalysisJob:
    """Создаёт completed job."""
    job = AnalysisJob.create(
        document_id=submission.document_id,
    )

    job.mark_queued()
    job.mark_processing()
    job.mark_completed()

    return job


@pytest.mark.asyncio
async def test_visualization_renders_selected_pages_once() -> None:
    """Все selected pages готовятся одним internal renderer call."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="14-16",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
    )

    request = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=b"pdf",
        cad_content=None,
    )

    renderer = FakeRenderer(
        pages=tuple(
            AnalysisPagePreview(
                page_number=page,
                width_points=841.89,
                height_points=595.28,
                image_base64="iVBORw0KGgo=",
            )
            for page in (
                14,
                15,
                16,
            )
        ),
    )

    job = completed_job(
        submission,
    )

    use_case = GetAnalysisVisualization(
        get_analysis_job=FakeGetJob(
            job,
        ),  # type: ignore[arg-type]
        artifact_store=FakeArtifactStore(
            request,
        ),  # type: ignore[arg-type]
        pdf_page_renderer=renderer,
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
            pages,
        )
        == 3
    )
    assert renderer.calls == 1
    assert renderer.page_spec == "14-16"


@pytest.mark.asyncio
async def test_cad_only_visualization_has_no_pdf_pages() -> None:
    """CAD-only result не запускает PDF renderer."""
    submission = AnalysisSubmission.create(
        pdf_present=False,
        cad_present=True,
        pages=None,
        pdf_file_name=None,
        cad_file_name="drawing.dxf",
    )

    request = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=None,
        cad_content=b"cad",
    )

    renderer = FakeRenderer(
        pages=(),
    )

    job = completed_job(
        submission,
    )

    use_case = GetAnalysisVisualization(
        get_analysis_job=FakeGetJob(
            job,
        ),  # type: ignore[arg-type]
        artifact_store=FakeArtifactStore(
            request,
        ),  # type: ignore[arg-type]
        pdf_page_renderer=renderer,
    )

    payload = await use_case.execute(
        job_id=job.id,
    )

    assert payload["pages"] == []
    assert renderer.calls == 0


@pytest.mark.asyncio
async def test_visualization_handles_50_synthetic_pages_in_one_call() -> None:
    """Synthetic load проверяет отсутствие N HTTP calls на N pages."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="1-50",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
    )

    request = AnalysisRequestArtifacts(
        submission=submission,
        pdf_content=b"pdf",
        cad_content=None,
    )

    renderer = FakeRenderer(
        pages=tuple(
            AnalysisPagePreview(
                page_number=page,
                width_points=1000.0,
                height_points=700.0,
                image_base64="iVBORw0KGgo=",
            )
            for page in range(
                1,
                51,
            )
        ),
    )

    job = completed_job(
        submission,
    )

    use_case = GetAnalysisVisualization(
        get_analysis_job=FakeGetJob(
            job,
        ),  # type: ignore[arg-type]
        artifact_store=FakeArtifactStore(
            request,
        ),  # type: ignore[arg-type]
        pdf_page_renderer=renderer,
    )

    payload: dict[
        str,
        Any,
    ] = await use_case.execute(
        job_id=job.id,
    )

    assert (
        len(
            payload["pages"],  # type: ignore[arg-type]
        )
        == 50
    )
    assert renderer.calls == 1
