# services/api-gateway/tests/unit/test_analysis_pdf_card_text.py

"""Tests compact PDF annotation-card text."""

from pdrd_api_gateway.application.use_cases.get_analysis_annotated_pdf import (
    GetAnalysisAnnotatedPdf,
)


def test_generic_comment_uses_evidence_in_visible_pdf_card() -> None:
    """Generic final comment не делает все visible cards одинаковыми."""
    finding = {
        "comment": ("На листе выявлено несоответствие, требующее проверки инженером."),
        "evidence": ("Схема T1.1 и T2.1 не содержит изображения запорного клапана."),
        "basis_sources": [
            {
                "source_file": ("СП 373.1325800.2018.pdf"),
                "page": 20,
            },
        ],
    }

    value = GetAnalysisAnnotatedPdf._annotation_card_text(
        finding,
    )

    assert "Схема T1.1 и T2.1" in value

    assert "На листе выявлено несоответствие" not in value

    assert "Норматив: СП 373.1325800.2018.pdf, стр. 20" in value


def test_specific_comment_remains_visible_pdf_card_text() -> None:
    """Содержательный comment не заменяется evidence."""
    finding = {
        "comment": ("На вводе отсутствует обозначение клапана."),
        "evidence": ("В области ввода видна линия T1.1."),
        "basis_sources": [],
    }

    value = GetAnalysisAnnotatedPdf._annotation_card_text(
        finding,
    )

    assert value == ("На вводе отсутствует обозначение клапана.")
