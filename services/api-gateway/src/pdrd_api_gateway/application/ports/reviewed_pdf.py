# services/api-gateway/src/pdrd_api_gateway/application/ports/reviewed_pdf.py

"""Независимые контракты утверждённой проекции и ограниченного кеша PDF."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
)
from pdrd_api_gateway.application.ports.review import ReviewContext


@dataclass(frozen=True, slots=True)
class ReviewedPdfFinding:
    """Принятое замечание: полный текст и только проверенная сервером геометрия."""

    number: int
    finding_id: str
    page_number: int
    origin: str
    experience_tag: str
    text: str
    normative_basis: str
    regions: tuple[AnalysisBoundingBox, ...]
    callout_box: AnalysisBoundingBox | None


@dataclass(frozen=True, slots=True)
class ReviewedPdfManifest:
    """Идентичность исходника и точная утверждённая редакция."""

    job_id: UUID
    document_id: UUID
    source_filename: str
    source_sha256: str
    revision: int
    digest: str
    findings: tuple[ReviewedPdfFinding, ...]


class ReviewedPdfSource(Protocol):
    """Порт Experience, который отказывает неутверждённому Review."""

    async def load(self, *, context: ReviewContext) -> ReviewedPdfManifest:
        """Возвращает проверенный внутренний контракт."""
        ...


class ReviewedPdfCache(Protocol):
    """Хранит не более одного результата на задание отдельно от автоматического PDF."""

    async def load(self, *, job_id: UUID, key: str) -> bytes | None:
        """Возвращает только результат с точным ключом текущей проекции."""
        ...

    async def save(self, *, job_id: UUID, key: str, content: bytes) -> None:
        """Атомарно заменяет содержимое вместе с ключом."""
        ...
