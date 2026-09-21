# services/api-gateway/src/pdrd_api_gateway/application/ports/analysis_pdf_export.py

"""Application contracts annotated PDF export."""

from dataclasses import dataclass
from typing import Protocol

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
)


@dataclass(frozen=True, slots=True)
class AnalysisPdfAnnotation:
    """Одно finding annotation для Document Service."""

    number: int
    finding_id: str
    page_number: int
    title: str
    content: str
    regions: tuple[
        AnalysisBoundingBox,
        ...,
    ]

    def as_dict(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает JSON-ready annotation."""
        return {
            "number": self.number,
            "finding_id": self.finding_id,
            "page_number": self.page_number,
            "title": self.title,
            "content": self.content,
            "regions": [region.as_dict() for region in self.regions],
        }


@dataclass(frozen=True, slots=True)
class AnalysisPdfReportField:
    """Одно поле PDF report."""

    label: str
    value: str

    def as_dict(
        self,
    ) -> dict[
        str,
        str,
    ]:
        """Возвращает JSON-ready field."""
        return {
            "label": self.label,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class AnalysisPdfReportFinding:
    """Один finding appended report."""

    title: str
    fields: tuple[
        AnalysisPdfReportField,
        ...,
    ]

    def as_dict(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает JSON-ready finding."""
        return {
            "title": self.title,
            "fields": [field.as_dict() for field in self.fields],
        }


@dataclass(frozen=True, slots=True)
class AnalysisPdfReport:
    """Полный appended PDF report."""

    title: str
    metadata: tuple[
        AnalysisPdfReportField,
        ...,
    ]
    summary: str
    findings: tuple[
        AnalysisPdfReportFinding,
        ...,
    ]
    limitations: tuple[
        str,
        ...,
    ]

    def as_dict(
        self,
    ) -> dict[
        str,
        object,
    ]:
        """Возвращает JSON-ready report."""
        return {
            "title": self.title,
            "metadata": [field.as_dict() for field in self.metadata],
            "summary": self.summary,
            "findings": [finding.as_dict() for finding in self.findings],
            "limitations": list(
                self.limitations,
            ),
        }


@dataclass(frozen=True, slots=True)
class AnalysisAnnotatedPdfDocument:
    """Готовый downloadable PDF."""

    content: bytes
    file_name: str


class AnalysisAnnotatedPdfRenderer(
    Protocol,
):
    """Контракт Document Service annotated PDF adapter."""

    async def render(
        self,
        *,
        pdf_content: bytes,
        file_name: str,
        annotations: tuple[
            AnalysisPdfAnnotation,
            ...,
        ],
        report: AnalysisPdfReport,
    ) -> bytes:
        """Строит annotated PDF."""
        ...
