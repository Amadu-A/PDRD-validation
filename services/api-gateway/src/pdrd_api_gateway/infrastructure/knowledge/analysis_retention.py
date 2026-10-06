# services/api-gateway/src/pdrd_api_gateway/infrastructure/knowledge/analysis_retention.py

"""Очистка отдельного ТЗ через защищённый внутренний контракт Knowledge Service."""

import httpx

from pdrd_api_gateway.domain.technical_assignment import TechnicalAssignmentSnapshot


class HttpTechnicalAssignmentRetention:
    """Не имеет доступа к файловой системе или БД Knowledge Service."""

    def __init__(
        self,
        *,
        base_url: str,
        internal_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Сохраняет адрес, отдельный серверный ключ и тестовый транспорт."""
        self.base_url = base_url.rstrip("/")
        self.key = internal_key
        self.transport = transport

    async def remove_source(
        self, snapshot: TechnicalAssignmentSnapshot, *, purge_metadata: bool
    ) -> None:
        """404 допускает повтор; другие отказы не отмечаются успешной очисткой."""
        if len(self.key) < 32:
            raise RuntimeError("Не настроен серверный ключ очистки ТЗ.")
        async with httpx.AsyncClient(
            timeout=30, trust_env=False, transport=self.transport
        ) as client:
            response = await client.delete(
                f"{self.base_url}/internal/v1/technical-assignments/{snapshot.technical_assignment_id}/retention",
                headers={"X-PDRD-Retention-Key": self.key},
                params={
                    "analysis_document_id": str(snapshot.analysis_document_id),
                    "sha256": snapshot.sha256,
                    "purge_metadata": str(purge_metadata).lower(),
                },
            )
        if response.status_code != 404:
            response.raise_for_status()
