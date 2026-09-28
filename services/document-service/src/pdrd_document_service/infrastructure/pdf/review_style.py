# services/document-service/src/pdrd_document_service/infrastructure/pdf/review_style.py

"""Оформление Gold и ограничение шрифта в заданной пользователем карточке."""

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True, slots=True)
class AnnotationPalette:
    """Цвета одного замечания без изменяемого состояния общего PDF writer."""

    stroke: tuple[float, float, float]
    fill: tuple[float, float, float]


def annotation_palette(origin: str) -> AnnotationPalette:
    """Выделяет пользовательские замечания жёлтым, сохраняя красный для VLM."""
    return (
        AnnotationPalette((0.65, 0.49, 0.08), (1.0, 0.97, 0.84))
        if origin == "manual"
        else AnnotationPalette((0.86, 0.12, 0.12), (1.0, 0.92, 0.92))
    )


def pinned_font_size(width: float, height: float, default: float) -> float:
    """Учитывает маленькие пользовательские рамки; полный текст остаётся в отчёте."""
    return max(4.0, min(max(default, 10.0), height / 3.5, width / 15.0))


def pinned_card_text(text: str) -> str:
    """Экранирует текст native FreeText, исключая интерпретацию замечания как HTML."""
    return "<p>" + escape(text).replace("\n", "<br>") + "</p>"


def pinned_card_style(font_size: float) -> str:
    """Отступы отделяют текст от номера, границы и значка полного замечания."""
    return f"font-family: sans-serif; font-size: {font_size}pt; color: #1f1f1f; margin: 6pt 24pt 6pt 28pt;"
