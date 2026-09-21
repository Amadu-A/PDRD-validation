# services/document-service/src/pdrd_document_service/application/ports/pdf_annotation.py

"""Application contracts построения PDF с инженерными аннотациями."""

from dataclasses import dataclass
from typing import Protocol

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


@dataclass(frozen=True, slots=True)
class PdfReportField:
    """Одно поле текстового отчёта."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class PdfReportFinding:
    """Текстовое представление одного finding."""

    title: str
    fields: tuple[
        PdfReportField,
        ...,
    ]


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
