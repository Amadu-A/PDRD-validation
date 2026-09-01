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

    @classmethod
    def create(
        cls,
        *,
        pdf_present: bool,
        cad_present: bool,
        pages: str | None,
        pdf_file_name: str | None,
        cad_file_name: str | None,
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

        return cls(
            document_id=uuid4(),
            source_mode=source_mode,
            pages=normalized_pages,
            pdf_file_name=(pdf_file_name if pdf_present else None),
            cad_file_name=(cad_file_name if cad_present else None),
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
