# services/document-service/src/pdrd_document_service/transport/http/schemas/pdf.py

"""HTTP schemas PDF extraction."""

from pydantic import BaseModel, ConfigDict

from pdrd_document_service.domain.pdf import PdfPageType


class PdfPageResponse(BaseModel):
    """Подготовленная PDF-страница."""

    model_config = ConfigDict(
        frozen=True,
    )

    page_number: int
    page_type: PdfPageType

    text: str

    width_points: float
    height_points: float

    image_base64: str


class PdfExtractionResponse(BaseModel):
    """Результат подготовки PDF."""

    model_config = ConfigDict(
        frozen=True,
    )

    file_name: str

    total_pages: int

    selected_pages: list[int]

    pages: list[PdfPageResponse]
