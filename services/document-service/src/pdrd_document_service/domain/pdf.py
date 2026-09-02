# services/document-service/src/pdrd_document_service/domain/pdf.py

"""Domain-модели и правила обработки PDF."""

import re
from dataclasses import dataclass
from enum import StrEnum


class PdfPageType(StrEnum):
    """Тип инженерного листа, определяемый без LLM."""

    TITLE = "title"
    SPECIFICATION = "specification"
    TABLE = "table"
    GENERAL_NOTES = "general_notes"
    GENERAL_DATA = "general_data"
    SCHEME = "scheme"
    DRAWING = "drawing"
    UNKNOWN = "unknown"


class InvalidPageSelectionError(ValueError):
    """Ошибка пользовательского диапазона PDF-страниц."""


@dataclass(frozen=True, slots=True)
class PdfPage:
    """Извлечённое представление одной физической PDF-страницы."""

    number: int
    page_type: PdfPageType
    text: str
    width_points: float
    height_points: float
    rendered_png: bytes


@dataclass(frozen=True, slots=True)
class PdfDocument:
    """Результат подготовки выбранных PDF-страниц."""

    total_pages: int
    pages: tuple[PdfPage, ...]

    @property
    def selected_page_numbers(self) -> tuple[int, ...]:
        """Возвращает физические номера подготовленных страниц."""
        return tuple(page.number for page in self.pages)


def parse_page_spec(
    page_spec: str | None,
    *,
    total_pages: int,
    max_selected_pages: int,
) -> tuple[int, ...]:
    """Разбирает строку вида ``1,3,5-8`` в номера PDF-страниц."""
    if total_pages < 1:
        raise InvalidPageSelectionError(
            "PDF не содержит страниц.",
        )

    normalized = (page_spec or "").strip()

    if not normalized:
        selected = tuple(
            range(
                1,
                total_pages + 1,
            )
        )

        _validate_page_limit(
            selected,
            max_selected_pages=max_selected_pages,
        )

        return selected

    result: set[int] = set()

    for raw_part in normalized.split(","):
        part = raw_part.strip()

        if not part:
            continue

        range_match = re.fullmatch(
            r"([1-9]\d*)\s*-\s*([1-9]\d*)",
            part,
        )

        if range_match is not None:
            start = int(
                range_match.group(1),
            )

            end = int(
                range_match.group(2),
            )

            if start > end:
                raise InvalidPageSelectionError(
                    f"Начало диапазона страниц больше конца: {part}.",
                )

            result.update(
                range(
                    start,
                    end + 1,
                )
            )

            continue

        if not re.fullmatch(
            r"[1-9]\d*",
            part,
        ):
            raise InvalidPageSelectionError(
                f"Некорректное описание PDF-страниц: {part}.",
            )

        result.add(
            int(part),
        )

    if not result:
        raise InvalidPageSelectionError(
            "Не удалось определить страницы для обработки.",
        )

    selected = tuple(
        sorted(result),
    )

    invalid_pages = tuple(
        page_number for page_number in selected if page_number > total_pages
    )

    if invalid_pages:
        invalid_text = ", ".join(str(page_number) for page_number in invalid_pages)

        raise InvalidPageSelectionError(
            "Страницы выходят за пределы PDF: "
            f"{invalid_text}. "
            f"Всего страниц: {total_pages}.",
        )

    _validate_page_limit(
        selected,
        max_selected_pages=max_selected_pages,
    )

    return selected


def classify_page(
    text: str,
    *,
    page_number: int,
) -> PdfPageType:
    """Классифицирует PDF-лист по извлечённому тексту без LLM."""
    normalized = re.sub(
        r"\s+",
        " ",
        text.lower(),
    )

    if page_number == 1 and any(
        marker in normalized
        for marker in (
            "рабочая документация",
            "проектная документация",
        )
    ):
        return PdfPageType.TITLE

    if any(
        marker in normalized
        for marker in (
            "спецификация оборудования",
            "спецификация изделий",
            "спецификация материалов",
        )
    ):
        return PdfPageType.SPECIFICATION

    if any(
        marker in normalized
        for marker in (
            "кабельный журнал",
            "ведомость объемов",
            "ведомость объёмов",
        )
    ):
        return PdfPageType.TABLE

    if "общие указания" in normalized:
        return PdfPageType.GENERAL_NOTES

    if any(
        marker in normalized
        for marker in (
            "общие данные",
            "ведомость документов",
            "ведомость ссылочных документов",
        )
    ):
        return PdfPageType.GENERAL_DATA

    if "схема" in normalized:
        return PdfPageType.SCHEME

    if any(
        marker in normalized
        for marker in (
            "план расположения",
            "план прокладки",
            "чертеж общего вида",
            "чертёж общего вида",
        )
    ):
        return PdfPageType.DRAWING

    return PdfPageType.UNKNOWN


def _validate_page_limit(
    selected_pages: tuple[int, ...],
    *,
    max_selected_pages: int,
) -> None:
    if len(selected_pages) <= max_selected_pages:
        return

    raise InvalidPageSelectionError(
        "Выбрано слишком много PDF-страниц: "
        f"{len(selected_pages)}. "
        f"Максимум: {max_selected_pages}.",
    )
