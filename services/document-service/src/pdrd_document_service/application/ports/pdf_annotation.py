# services/document-service/src/pdrd_document_service/application/ports/pdf_annotation.py

"""Application contracts построения PDF с инженерными аннотациями."""

from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pdrd_document_service.domain.pdf import (
    PdfNormalizedBoundingBox,
)


@dataclass(frozen=True, slots=True)
class PdfFindingAnnotation:
    """Одно замечание, которое можно разместить на исходном PDF."""

    number: int
    finding_id: str
    page_number: int
    title: str
    content: str
    regions: tuple[
        PdfNormalizedBoundingBox,
        ...,
    ]
    callout_box: PdfNormalizedBoundingBox | None = None
    origin: Literal["vlm", "manual"] = "vlm"


@dataclass(frozen=True, slots=True)
class PdfReportField:
    """Одно поле текстового отчёта."""

    label: str
    value: str
    href: str | None = None

    def __post_init__(self):
        """PDF не должен исполнять произвольную схему ссылки из текста замечания."""
        if self.href:
            parsed = urlsplit(self.href)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Допустима только HTTP(S)-ссылка без учётных данных.")


@dataclass(frozen=True, slots=True)
class PdfReportFinding:
    """Текстовое представление одного finding."""

    title: str
    fields: tuple[
        PdfReportField,
        ...,
    ]
    origin: Literal["vlm", "manual"] = "vlm"


@dataclass(frozen=True, slots=True)
class PdfTextReport:
    """Отчёт, добавляемый отдельными страницами в конец PDF."""

    title: str
    metadata: tuple[
        PdfReportField,
        ...,
    ]
    summary: str
    findings: tuple[
        PdfReportFinding,
        ...,
    ]
    limitations: tuple[
        str,
        ...,
    ]


class PdfAnnotationWriter(Protocol):
    """Контракт инфраструктурного PDF writer."""

    def build(
        self,
        *,
        content: bytes,
        annotations: tuple[
            PdfFindingAnnotation,
            ...,
        ],
        report: PdfTextReport,
    ) -> bytes:
        """Возвращает исходный PDF с annotations и append-only отчётом."""
        ...
