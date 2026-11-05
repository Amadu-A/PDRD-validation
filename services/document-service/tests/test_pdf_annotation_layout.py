# services/document-service/tests/test_pdf_annotation_layout.py

"""Раскладка PDF-карточек за пределами исходной страницы."""

import fitz
from pdrd_document_service.infrastructure.pdf.sidebar import plan_cards


def test_cards_are_placed_to_right_without_covering_source():
    """Даже сохранённые GUI-позиции не нужны локальной раскладке PDF."""
    original = fitz.Rect(0, 0, 595, 842)
    cards, added_width = plan_cards(
        original, [("Проверить расчёт и обоснование", 8.5)] * 20
    )
    assert added_width > 0
    assert all(
        card.rect.x0 > original.x1 and not card.rect.intersects(original)
        for card in cards
    )
    assert all(card.font_size == 8.5 for card in cards)
