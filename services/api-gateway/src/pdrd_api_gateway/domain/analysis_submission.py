# services/api-gateway/src/pdrd_api_gateway/domain/analysis_submission.py

"""Domain-модель пользовательской заявки на анализ."""

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4


class AnalysisSourceMode(StrEnum):
    """Режим исходных документов анализа."""

    PDF_ONLY = "pdf_only"
    CAD_ONLY = "cad_only"
    PDF_CAD = "pdf_cad"


class InvalidAnalysisSubmissionError(ValueError):
    """Ошибка состава пользовательской заявки на анализ."""


@dataclass(frozen=True, slots=True)
class AnalysisSubmission:
    """Метаданные сохранённых исходных документов анализа."""

    document_id: UUID
    source_mode: AnalysisSourceMode

    pages: str | None

    pdf_file_name: str | None
    cad_file_name: str | None

    use_explanatory_note: bool
    note_start_page: int | None
    note_end_page: int | None

    @classmethod
    def create(
        cls,
        *,
        pdf_present: bool,
        cad_present: bool,
        pages: str | None,
        pdf_file_name: str | None,
        cad_file_name: str | None,
        use_explanatory_note: bool = False,
        note_start_page: str | int | None = None,
        note_end_page: str | int | None = None,
    ) -> "AnalysisSubmission":
        """Создаёт и валидирует заявку на анализ."""
        source_mode = cls._resolve_source_mode(
            pdf_present=pdf_present,
            cad_present=cad_present,
        )

        normalized_pages = cls._normalize_pages(
            source_mode=source_mode,
            pages=pages,
        )

        (
            normalized_use_note,
            normalized_note_start,
            normalized_note_end,
        ) = cls._normalize_explanatory_note(
            source_mode=source_mode,
            enabled=use_explanatory_note,
            start_page=note_start_page,
            end_page=note_end_page,
        )

        return cls(
            document_id=uuid4(),
            source_mode=source_mode,
            pages=normalized_pages,
            pdf_file_name=(pdf_file_name if pdf_present else None),
            cad_file_name=(cad_file_name if cad_present else None),
            use_explanatory_note=normalized_use_note,
            note_start_page=normalized_note_start,
            note_end_page=normalized_note_end,
        )

    @staticmethod
    def _resolve_source_mode(
        *,
        pdf_present: bool,
        cad_present: bool,
    ) -> AnalysisSourceMode:
        """Определяет режим анализа по составу файлов."""
        if pdf_present and cad_present:
            return AnalysisSourceMode.PDF_CAD

        if pdf_present:
            return AnalysisSourceMode.PDF_ONLY

        if cad_present:
            return AnalysisSourceMode.CAD_ONLY

        raise InvalidAnalysisSubmissionError(
            "Необходимо загрузить PDF и/или DWG/DXF.",
        )

    @staticmethod
    def _normalize_pages(
        *,
        source_mode: AnalysisSourceMode,
        pages: str | None,
    ) -> str | None:
        """Нормализует пользовательский выбор PDF-страниц."""
        normalized = pages.strip() if pages is not None else ""

        if source_mode is AnalysisSourceMode.CAD_ONLY:
            return None

        if source_mode is AnalysisSourceMode.PDF_CAD:
            if not re.fullmatch(
                r"[1-9]\d*",
                normalized,
            ):
                raise InvalidAnalysisSubmissionError(
                    "Для режима PDF + CAD необходимо "
                    "указать ровно одну положительную "
                    "PDF-страницу.",
                )

            return normalized

        return normalized or None

    @staticmethod
    def _normalize_explanatory_note(
        *,
        source_mode: AnalysisSourceMode,
        enabled: bool,
        start_page: str | int | None,
        end_page: str | int | None,
    ) -> tuple[
        bool,
        int | None,
        int | None,
    ]:
        """Проверяет пользовательские параметры контекста ПЗ."""
        if not enabled:
            return (
                False,
                None,
                None,
            )

        if source_mode is AnalysisSourceMode.CAD_ONLY:
            raise InvalidAnalysisSubmissionError(
                "Контекст ПЗ доступен только при наличии PDF.",
            )

        if (
            start_page is None
            or end_page is None
            or not str(start_page).strip()
            or not str(end_page).strip()
        ):
            raise InvalidAnalysisSubmissionError(
                "При включённом контексте ПЗ необходимо "
                "указать начальную и конечную страницы.",
            )

        try:
            start = int(
                str(start_page).strip(),
            )

            end = int(
                str(end_page).strip(),
            )

        except ValueError as error:
            raise InvalidAnalysisSubmissionError(
                "Номера страниц ПЗ должны быть целыми числами.",
            ) from error

        if start < 1 or end < 1:
            raise InvalidAnalysisSubmissionError(
                "Номера страниц ПЗ должны быть положительными.",
            )

        if end <= start:
            raise InvalidAnalysisSubmissionError(
                "Конечная страница ПЗ должна быть больше начальной.",
            )

        return (
            True,
            start,
            end,
        )
