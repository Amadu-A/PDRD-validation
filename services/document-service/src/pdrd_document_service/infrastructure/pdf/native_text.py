# services/document-service/src/pdrd_document_service/infrastructure/pdf/native_text.py

"""Исходный текст PDF и AutoCAD SHX с геометрией, без OCR и пользовательских замечаний."""

import re

import fitz


def shx_spans(page: fitz.Page) -> list[dict]:
    """Читает только подписи AutoCAD SHX; комментарии Review не становятся текстом проекта."""
    native = page.get_text("words", sort=True)
    spans, seen = [], set()
    for annotation in page.annots() or ():
        info = annotation.info
        if info.get("title", "").casefold() != "autocad shx text":
            continue
        text = info.get("content", "").strip()
        box = fitz.Rect(annotation.rect)
        if not text or box.is_empty or box.is_infinite:
            continue
        key = (text, tuple(box))
        if key in seen:
            continue
        seen.add(key)
        # Дублирующий текстовый слой не считается вторым независимым объектом.
        covered = " ".join(
            str(word[4]) for word in native if box.contains(fitz.Rect(word[:4]))
        )
        if re.sub(r"\s+", "", covered) == re.sub(r"\s+", "", text):
            continue
        visual = box * page.rotation_matrix if page.rotation else box
        spans.append(
            {
                "text": text,
                "bbox": tuple(box),
                "visual_bbox": tuple(visual),
                "xref": annotation.xref,
            }
        )
    return sorted(
        spans,
        key=lambda span: (round(span["visual_bbox"][1], 1), span["visual_bbox"][0]),
    )


def native_text(page: fitz.Page, *, spans: list[dict] | None = None) -> str:
    """Добавляет все исходные SHX-подписи к обычному тексту, не переписывая PDF."""
    text = page.get_text("text", sort=True)
    spans = shx_spans(page) if spans is None else spans
    return text + ("\n" + "\n".join(span["text"] for span in spans) if spans else "")


def cell_text(page: fitz.Page, box, spans: list[dict], *, visual: bool = False) -> str:
    """Связывает исходную подпись с ячейкой лишь при геометрическом покрытии её центра."""
    if box is None:
        return ""
    region = fitz.Rect(box)
    values = []
    for span in spans:
        source = fitz.Rect(span["visual_bbox"] if visual else span["bbox"])
        overlap = source & region
        if source.get_area() > 0 and overlap.get_area() / source.get_area() >= 0.8:
            reading_box = fitz.Rect(span["visual_bbox"])
            values.append((reading_box.y0, reading_box.x0, span["text"]))
    return "\n".join(value[2] for value in sorted(values))
