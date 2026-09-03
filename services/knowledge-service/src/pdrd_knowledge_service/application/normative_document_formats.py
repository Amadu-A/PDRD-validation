# services/knowledge-service/src/pdrd_knowledge_service/application/normative_document_formats.py

"""Поддерживаемые форматы managed нормативных документов."""

PDF_EXTENSION = ".pdf"

DOC_EXTENSION = ".doc"

DOCX_EXTENSION = ".docx"


PDF_MIME_TYPE = "application/pdf"

DOC_MIME_TYPE = "application/msword"

DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


SUPPORTED_DOCUMENT_MIME_BY_EXTENSION = {
    PDF_EXTENSION: PDF_MIME_TYPE,
    DOC_EXTENSION: DOC_MIME_TYPE,
    DOCX_EXTENSION: DOCX_MIME_TYPE,
}


WORD_MIME_TYPES = frozenset(
    {
        DOC_MIME_TYPE,
        DOCX_MIME_TYPE,
    }
)


def is_word_mime_type(
    mime_type: str,
) -> bool:
    """Проверяет, является ли документ Word-файлом."""
    return mime_type in WORD_MIME_TYPES


def preview_storage_key(
    storage_key: str,
) -> str:
    """Возвращает deterministic storage key PDF-preview."""
    return f"{storage_key}.preview.pdf"
