# services/api-gateway/tests/unit/test_analysis_submission.py

"""Unit tests пользовательской заявки анализа."""

import pytest
from pdrd_api_gateway.domain.analysis_submission import (
    AnalysisSourceMode,
    AnalysisSubmission,
    InvalidAnalysisSubmissionError,
)


def test_pdf_only_submission() -> None:
    """Определяет PDF-only режим."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="1,3-5",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
    )

    assert submission.source_mode is AnalysisSourceMode.PDF_ONLY

    assert submission.pages == "1,3-5"


def test_cad_only_submission_ignores_pages() -> None:
    """CAD-only не использует выбор PDF-страниц."""
    submission = AnalysisSubmission.create(
        pdf_present=False,
        cad_present=True,
        pages="7",
        pdf_file_name=None,
        cad_file_name="drawing.dxf",
    )

    assert submission.source_mode is AnalysisSourceMode.CAD_ONLY

    assert submission.pages is None


def test_pdf_cad_requires_exactly_one_page() -> None:
    """PDF+CAD требует ровно один номер PDF-страницы."""
    with pytest.raises(
        InvalidAnalysisSubmissionError,
    ):
        AnalysisSubmission.create(
            pdf_present=True,
            cad_present=True,
            pages="1-2",
            pdf_file_name="drawing.pdf",
            cad_file_name="drawing.dxf",
        )


def test_submission_requires_source_file() -> None:
    """Не допускает заявку без исходных файлов."""
    with pytest.raises(
        InvalidAnalysisSubmissionError,
    ):
        AnalysisSubmission.create(
            pdf_present=False,
            cad_present=False,
            pages=None,
            pdf_file_name=None,
            cad_file_name=None,
        )
