# services/equipment-search-service/src/pdrd_equipment_search_service/domain/diagnostics.py

"""Безопасные причины отказа без адресов с токенами и текста исключений."""

from enum import StrEnum


class ReasonCode(StrEnum):
    """Стабильный контракт диагностики EQ для журнала и интерфейса."""

    NETWORK_ERROR = "network_error"
    DNS_ERROR = "dns_error"
    TLS_ERROR = "tls_error"
    HTTP_ERROR = "http_error"
    URL_BLOCKED = "url_blocked"
    REDIRECT_BLOCKED = "redirect_blocked"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_FILE = "unsupported_file"
    MANUFACTURER_MISMATCH = "manufacturer_mismatch"
    MANUFACTURER_UNKNOWN = "manufacturer_unknown"
    MODEL_MISMATCH = "model_mismatch"
    VARIANT_MISMATCH = "variant_mismatch"
    ARTICLE_MISMATCH = "article_mismatch"
    EXTRACTION_ERROR = "extraction_error"
    FACTS_UNAVAILABLE = "facts_unavailable"
    QUERY_BUDGET = "query_budget_exhausted"
    DOWNLOAD_BUDGET = "download_budget_exhausted"
    VISION_BUDGET = "vision_budget_exhausted"
    SOURCE_UNVERIFIED = "source_unverified"
    SOURCE_BLOCKED = "source_blocked"
    NOT_FOUND = "document_not_found"
    NOT_CHECKED = "not_checked"
    MODEL_UNCERTAIN = "model_uncertain"
    TIMEOUT = "operation_timeout"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


class DocumentOperationError(ValueError):
    """Передаёт машинный код, не заставляя приложение разбирать сообщение драйвера."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Сохраняет безопасный код и локальное сообщение для диагностики."""
        super().__init__(message)
        self.code = code or getattr(self, "default_code", ReasonCode.INTERNAL_ERROR)
