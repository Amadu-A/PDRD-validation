# services/api-gateway/src/pdrd_api_gateway/application/ports/normative_catalog.py

"""Application port чтения managed normative catalog."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class NormativeCatalogReadError(RuntimeError):
    """Knowledge Service недоступен или вернул некорректный ответ."""


class NormativeCatalogNotFoundError(LookupError):
    """Запрошенная сущность normative catalog не найдена."""


@dataclass(frozen=True, slots=True)
class NormativeSectionRecord:
    """Необходимые Gateway данные нормативного раздела."""

    section_id: UUID

    system_prompt: str


@dataclass(frozen=True, slots=True)
class NormativeDocumentRecord:
    """Необходимые Gateway данные нормативного документа."""

    document_id: UUID
    section_id: UUID

    ready_for_analysis: bool


class NormativeCatalogReader(Protocol):
    """Контракт чтения normative catalog через Knowledge Service."""

    async def get_section(
        self,
        *,
        section_id: UUID,
    ) -> NormativeSectionRecord:
        """Возвращает section и сохранённый system prompt."""
        ...

    async def list_documents(
        self,
        *,
        section_id: UUID,
    ) -> tuple[
        NormativeDocumentRecord,
        ...,
    ]:
        """Возвращает документы указанного section."""
        ...
