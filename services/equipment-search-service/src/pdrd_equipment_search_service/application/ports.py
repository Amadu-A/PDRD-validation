# services/equipment-search-service/src/pdrd_equipment_search_service/application/ports.py

"""Контракты каталога, метапоиска, загрузки и анализа документа."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DocumentSnapshot,
    DownloadedDocument,
    EquipmentIdentity,
    SearchHit,
    SourceDomain,
)


class EquipmentCatalog(Protocol):
    """Хранит доверие, локальные документы и версии."""

    async def source_blocked(self, hostname: str) -> bool:
        """Запрещает заблокированный домен при неизвестном производителе."""

    async def find_document(
        self, identity: EquipmentIdentity
    ) -> DocumentSnapshot | None:
        """Находит применимый сохранённый snapshot."""

    async def source_for(self, manufacturer: str, hostname: str) -> SourceDomain | None:
        """Возвращает точное разрешение производителя и hostname."""

    async def trusted_hosts(self, manufacturer: str) -> tuple[str, ...]:
        """Возвращает подтверждённые hostname производителя."""

    async def manufacturer_aliases(self, manufacturer: str) -> tuple[str, ...]:
        """Читает серверный реестр подтверждённых написаний производителя."""

    async def manufacturer_for_host(self, hostname: str) -> str | None:
        """Возвращает производителя только при однозначном точном домене."""

    async def manufacturer_profiles(self) -> tuple[dict[str, Any], ...]:
        """Читает имена и aliases из собственного реестра."""

    async def register_pending(
        self, manufacturer: str, hostname: str, example_url: str, model: str
    ) -> None:
        """Фиксирует неизвестный hostname без выдачи доверия."""

    async def document_pages(
        self,
        source_id: str,
    ) -> tuple[tuple[int, str], ...]:
        """Читает сохранённый текст страниц без повторной загрузки."""

    async def document_vision_facts(self, source_id: str) -> tuple[dict[str, Any], ...]:
        """Читает визуальную транскрипцию неизменённого snapshot."""

    async def save_document(
        self,
        identity: EquipmentIdentity,
        source_url: str,
        downloaded: DownloadedDocument,
        inspection: DocumentInspection,
        trust_status: str,
    ) -> DocumentSnapshot:
        """Создаёт или переиспользует неизменяемый snapshot по SHA-256."""


class EquipmentFactIndex(Protocol):
    """Получает версионированные EQ Facts через Knowledge Service."""

    async def extract_or_get(
        self,
        identity: EquipmentIdentity,
        snapshot: DocumentSnapshot,
        pages: tuple[tuple[int, str], ...],
        *,
        vision_facts: tuple[dict[str, Any], ...] = (),
    ) -> tuple[str, tuple[dict[str, Any], ...]]:
        """Возвращает статус и факты конкретного snapshot."""


class WebSearch(Protocol):
    """Ищет ссылки через локальный SearXNG."""

    async def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        """Возвращает ограниченный список ссылок."""


class DocumentDownloader(Protocol):
    """Безопасно загружает HTTP(S)-документ."""

    async def download(
        self,
        url: str,
        *,
        allow_http: bool = False,
        allowed_redirect_hosts: tuple[str, ...] = (),
    ) -> DownloadedDocument:
        """Возвращает документ с проверкой URL, DNS, redirects и размера."""


class DocumentInspector(Protocol):
    """Проверяет модель и ревизию документа до допуска в evidence."""

    async def inspect(
        self, identity: EquipmentIdentity, document: DownloadedDocument
    ) -> DocumentInspection:
        """Отклоняет документы чужой модели."""


class EquipmentVisionInspector(Protocol):
    """Адресно проверяет выбранные страницы скана в рамках общего бюджета."""

    async def inspect(
        self,
        identity: EquipmentIdentity,
        document: DownloadedDocument,
        inspection: DocumentInspection,
        *,
        budget: EquipmentVisionBudget,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> DocumentInspection:
        """Возвращает только проверенную маркировку и осторожные EQ Facts."""
