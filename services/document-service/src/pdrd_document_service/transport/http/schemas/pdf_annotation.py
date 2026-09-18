# services/document-service/src/pdrd_document_service/transport/http/schemas/pdf_annotation.py

"""HTTP schemas внутреннего annotated PDF contract."""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from pdrd_document_service.application.ports.pdf_annotation import (
    PdfFindingAnnotation,
    PdfReportField,
    PdfReportFinding,
    PdfTextReport,
)
from pdrd_document_service.domain.pdf import (
    PdfNormalizedBoundingBox,
)


class PdfAnnotationBoundingBoxRequest(
    BaseModel,
):
    """Нормализованный bbox annotation."""

    model_config = ConfigDict(
        frozen=True,
    )

    x_min: int = Field(
        ge=0,
        le=1000,
    )

    y_min: int = Field(
        ge=0,
        le=1000,
    )

    x_max: int = Field(
        ge=0,
        le=1000,
    )

    y_max: int = Field(
        ge=0,
        le=1000,
    )

    @model_validator(
        mode="after",
    )
    def validate_area(
        self,
    ) -> "PdfAnnotationBoundingBoxRequest":
        """Проверяет положительную площадь bbox."""
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError(
                "Annotation bbox должен иметь положительную площадь.",
            )

        return self

    def to_domain(
        self,
    ) -> PdfNormalizedBoundingBox:
        """Преобразует transport bbox в domain value object."""
        return PdfNormalizedBoundingBox(
            x_min=self.x_min,
            y_min=self.y_min,
            x_max=self.x_max,
            y_max=self.y_max,
        )


class PdfFindingAnnotationRequest(
    BaseModel,
):
    """Одно visual finding для PDF annotation."""

    model_config = ConfigDict(
        frozen=True,
    )

    number: int = Field(
        ge=1,
    )

    finding_id: str = Field(
        min_length=1,
    )

    page_number: int = Field(
        ge=1,
    )

    title: str = Field(
        min_length=1,
    )

    content: str = Field(
        min_length=1,
    )

    regions: list[PdfAnnotationBoundingBoxRequest] = Field(
        default_factory=list,
    )

    def to_domain(
        self,
    ) -> PdfFindingAnnotation:
        """Преобразует transport item в application DTO."""
        return PdfFindingAnnotation(
            number=self.number,
            finding_id=self.finding_id,
            page_number=self.page_number,
            title=self.title,
            content=self.content,
            regions=tuple(region.to_domain() for region in self.regions),
        )


class PdfReportFieldRequest(
    BaseModel,
):
    """Одно label/value поле appended report."""

    model_config = ConfigDict(
        frozen=True,
    )

    label: str = Field(
        min_length=1,
    )

    value: str = Field(
        min_length=1,
    )

    def to_domain(
        self,
    ) -> PdfReportField:
        """Преобразует transport field."""
        return PdfReportField(
            label=self.label,
            value=self.value,
        )


class PdfReportFindingRequest(
    BaseModel,
):
    """Один finding текстового отчёта."""

    model_config = ConfigDict(
        frozen=True,
    )

    title: str = Field(
        min_length=1,
    )

    fields: list[PdfReportFieldRequest]

    def to_domain(
        self,
    ) -> PdfReportFinding:
        """Преобразует finding report DTO."""
        return PdfReportFinding(
            title=self.title,
            fields=tuple(field.to_domain() for field in self.fields),
        )


class PdfTextReportRequest(
    BaseModel,
):
    """Append-only report contract."""

    model_config = ConfigDict(
        frozen=True,
    )

    title: str = Field(
        min_length=1,
    )

    metadata: list[PdfReportFieldRequest]

    summary: str = ""

    findings: list[PdfReportFindingRequest]

    limitations: list[str] = Field(
        default_factory=list,
    )

    def to_domain(
        self,
    ) -> PdfTextReport:
        """Преобразует HTTP report в application DTO."""
        return PdfTextReport(
            title=self.title,
            metadata=tuple(field.to_domain() for field in self.metadata),
            summary=self.summary,
            findings=tuple(finding.to_domain() for finding in self.findings),
            limitations=tuple(
                limitation for limitation in self.limitations if limitation.strip()
            ),
        )


class PdfAnnotatedDocumentRequest(
    BaseModel,
):
    """JSON-part внутреннего multipart annotation request."""

    model_config = ConfigDict(
        frozen=True,
    )

    annotations: list[PdfFindingAnnotationRequest] = Field(
        default_factory=list,
    )

    report: PdfTextReportRequest
