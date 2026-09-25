# services/document-service/tests/test_pdf_annotation_layout.py

"""Regression tests collision-aware PDF annotation card placement."""

import fitz
from pdrd_document_service.infrastructure.pdf.annotator import (
    PyMuPdfAnnotationWriter,
)


def test_card_score_strongly_penalizes_original_text_overlap() -> None:
    """Card поверх source text должен проигрывать эквивалентному clear месту."""
    writer = PyMuPdfAnnotationWriter()

    anchor = fitz.Rect(
        250,
        250,
        270,
        270,
    )

    candidate = fitz.Rect(
        300,
        200,
        430,
        280,
    )

    score_without_text = writer._candidate_score(
        candidate=candidate,
        anchor=anchor,
        occupied_cards=[],
        all_regions=(),
        text_regions=(),
    )

    score_over_text = writer._candidate_score(
        candidate=candidate,
        anchor=anchor,
        occupied_cards=[],
        all_regions=(),
        text_regions=(
            fitz.Rect(
                300,
                200,
                430,
                280,
            ),
        ),
    )

    assert score_over_text > score_without_text

    assert score_over_text - score_without_text > 1000
