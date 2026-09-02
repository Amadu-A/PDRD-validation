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

    assert submission.use_explanatory_note is False

    assert submission.note_start_page is None
    assert submission.note_end_page is None


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


def test_pdf_submission_accepts_explanatory_note() -> None:
    """PDF позволяет включить корректный диапазон ПЗ."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=False,
        pages="12",
        pdf_file_name="drawing.pdf",
        cad_file_name=None,
        use_explanatory_note=True,
        note_start_page="2",
        note_end_page="8",
    )

    assert submission.use_explanatory_note is True

    assert submission.note_start_page == 2
    assert submission.note_end_page == 8


def test_pdf_cad_accepts_explanatory_note() -> None:
    """PDF+CAD также может использовать ПЗ из PDF."""
    submission = AnalysisSubmission.create(
        pdf_present=True,
        cad_present=True,
        pages="12",
        pdf_file_name="drawing.pdf",
        cad_file_name="drawing.dxf",
        use_explanatory_note=True,
        note_start_page=2,
        note_end_page=8,
    )

    assert submission.source_mode is AnalysisSourceMode.PDF_CAD

    assert submission.use_explanatory_note is True


def test_cad_only_rejects_explanatory_note() -> None:
    """CAD-only не допускает контекст ПЗ."""
    with pytest.raises(
        InvalidAnalysisSubmissionError,
        match="только при наличии PDF",
    ):
        AnalysisSubmission.create(
            pdf_present=False,
            cad_present=True,
            pages=None,
            pdf_file_name=None,
            cad_file_name="drawing.dxf",
            use_explanatory_note=True,
            note_start_page=1,
            note_end_page=5,
        )


def test_explanatory_note_requires_both_pages() -> None:
    """ПЗ требует начало и конец диапазона."""
    with pytest.raises(
        InvalidAnalysisSubmissionError,
        match="начальную и конечную",
    ):
        AnalysisSubmission.create(
            pdf_present=True,
            cad_present=False,
            pages="10",
            pdf_file_name="drawing.pdf",
            cad_file_name=None,
            use_explanatory_note=True,
            note_start_page="2",
            note_end_page=None,
        )


def test_explanatory_note_requires_positive_pages() -> None:
    """Номера страниц ПЗ должны быть положительными."""
    with pytest.raises(
        InvalidAnalysisSubmissionError,
        match="положительными",
    ):
        AnalysisSubmission.create(
            pdf_present=True,
            cad_present=False,
            pages="10",
            pdf_file_name="drawing.pdf",
            cad_file_name=None,
            use_explanatory_note=True,
            note_start_page="0",
            note_end_page="4",
        )


def test_explanatory_note_end_must_be_greater() -> None:
    """Конец диапазона ПЗ должен быть больше начала."""
    with pytest.raises(
        InvalidAnalysisSubmissionError,
        match="больше начальной",
    ):
        AnalysisSubmission.create(
            pdf_present=True,
            cad_present=False,
            pages="10",
            pdf_file_name="drawing.pdf",
            cad_file_name=None,
            use_explanatory_note=True,
            note_start_page="5",
            note_end_page="5",
        )
