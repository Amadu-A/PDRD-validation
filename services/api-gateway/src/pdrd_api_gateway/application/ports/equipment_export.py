# services/api-gateway/src/pdrd_api_gateway/application/ports/equipment_export.py

"""Порты самостоятельной поставки EQ без доступа Gateway к чужому volume."""

from typing import Protocol


class EquipmentExportDocuments(Protocol):
    """Чтение сохранённых байтов и рендер отчёта через владельцев данных."""

    async def document(self, source_id: str) -> tuple[bytes, str]:
        """Возвращает immutable snapshot по внутреннему ключу."""

    async def report_pdf(self, job_id: str, revision: int, rows: list[dict]) -> bytes:
        """Вызывает Document Service для текущей закреплённой версии EQ."""
