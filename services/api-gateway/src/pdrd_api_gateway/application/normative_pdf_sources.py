# services/api-gateway/src/pdrd_api_gateway/application/normative_pdf_sources.py

"""Исходные нормативные ссылки PDF: точная цитата, редакция, пункт и физическая страница."""

from urllib.parse import urlsplit
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisPdfReportField,
)


def normative_pdf_fields(sources, *, public_origin: str = ""):
    """Не создаёт фиктивный URL, если доверенный публичный адрес не настроен."""
    fields = []
    origin = urlsplit(public_origin)
    valid_origin = (
        origin.scheme in {"http", "https"}
        and bool(origin.hostname)
        and not origin.username
        and not origin.password
        and not origin.path.strip("/")
        and not origin.query
        and not origin.fragment
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        atomic = source.get("atomic_requirement") or {}
        if not isinstance(atomic, dict):
            atomic = {}
        code = (
            atomic.get("document_code")
            or source.get("source_file")
            or "Нормативный документ"
        )
        clause = ".".join(
            value
            for key in ("clause", "subclause")
            if isinstance(value := atomic.get(key), str) and value
        )
        page = source.get("page")
        value = f"{source.get('source_id', '')}: {code}"
        if atomic.get("edition"):
            value += f", редакция {atomic['edition']}"
        if clause:
            value += f", п. {clause}"
        value += f", физическая стр. {page}.\n{source.get('text', '')}"
        if atomic:
            status = atomic.get("normative_status") or {}
            if not isinstance(status, dict):
                status = {}
            edition_status = {
                "current": "действующая",
                "withdrawn": "отменена",
                "disputed": "спорная",
            }.get(status.get("edition_status"), "не подтверждена")
            binding = {
                "mandatory": "подтверждена",
                "voluntary": "добровольное применение",
                "disputed": "спорная",
            }.get(status.get("requirement_binding"), "не подтверждена")
            evidence_ids = status.get("evidence_ids", [])
            evidence = (
                ", ".join(value for value in evidence_ids if isinstance(value, str))
                if isinstance(evidence_ids, list)
                else ""
            )
            evidence = evidence or "не представлены"
            value += (
                f"\nТребование: {atomic.get('requirement_id', '')}; поколение: {atomic.get('generation_id', '')}."
                f"\nОценка статуса на {status.get('assessed_on', '')}: "
                f"редакция — {edition_status}; обязательность — {binding}. Свидетельства: {evidence}."
            )
        href = None
        try:
            document = str(UUID(str(source.get("document_id"))))
            physical_page = int(page)
            if valid_origin and physical_page > 0:
                href = f"{public_origin.rstrip('/')}/api/v1/normative/documents/{document}/content#page={physical_page}"
        except (ValueError, TypeError):
            pass
        fields.append(
            AnalysisPdfReportField("Нормативное основание [N]", value, href=href)
        )
    return tuple(fields)
