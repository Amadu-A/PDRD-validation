# services/document-service/src/pdrd_document_service/domain/project_context.py

"""Domain-модели извлечения контекста Пояснительной записки."""

from dataclasses import dataclass


class InvalidExplanatoryNoteRangeError(ValueError):
    """Ошибка физического диапазона страниц Пояснительной записки."""


@dataclass(frozen=True, slots=True)
class PdfTextPage:
    """Текст одной физической PDF-страницы без render payload."""

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class ExplanatoryNoteContext:
    """Извлечённый диапазон Пояснительной записки."""

    enabled: bool

    start_page: int | None
    end_page: int | None

    pages: tuple[PdfTextPage, ...]

    @classmethod
    def disabled(cls) -> "ExplanatoryNoteContext":
        """Возвращает отключённый контекст ПЗ."""
        return cls(
            enabled=False,
            start_page=None,
            end_page=None,
            pages=(),
        )

    @property
    def pages_count(self) -> int:
        """Возвращает количество выбранных страниц."""
        return len(
            self.pages,
        )


def parse_explanatory_note_range(
    *,
    enabled: bool,
    start_page: str | int | None,
    end_page: str | int | None,
    total_pages: int,
    max_context_pages: int,
) -> tuple[int, ...]:
    """Проверяет диапазон ПЗ относительно физического PDF."""
    if not enabled:
        return ()

    if start_page is None or end_page is None:
        raise InvalidExplanatoryNoteRangeError(
            "При включённом контексте ПЗ необходимо "
            "указать начальную и конечную страницы.",
        )

    start_text = str(
        start_page,
    ).strip()

    end_text = str(
        end_page,
    ).strip()

    if not start_text or not end_text:
        raise InvalidExplanatoryNoteRangeError(
            "При включённом контексте ПЗ необходимо "
            "указать начальную и конечную страницы.",
        )

    try:
        start = int(
            start_text,
        )

        end = int(
            end_text,
        )
    except ValueError as error:
        raise InvalidExplanatoryNoteRangeError(
            "Номера страниц ПЗ должны быть целыми числами.",
        ) from error

    if start < 1 or end < 1:
        raise InvalidExplanatoryNoteRangeError(
            "Номера страниц ПЗ должны быть положительными.",
        )

    if end <= start:
        raise InvalidExplanatoryNoteRangeError(
            "Конечная страница ПЗ должна быть больше начальной.",
        )

    if start > total_pages or end > total_pages:
        raise InvalidExplanatoryNoteRangeError(
            "Диапазон ПЗ выходит за пределы документа. "
            f"В PDF всего страниц: {total_pages}; "
            f"получен диапазон: {start}-{end}.",
        )

    pages = tuple(
        range(
            start,
            end + 1,
        )
    )

    if len(pages) > max_context_pages:
        raise InvalidExplanatoryNoteRangeError(
            "Выбрано слишком много страниц ПЗ: "
            f"{len(pages)}. "
            f"Максимум: {max_context_pages}.",
        )

    return pages
