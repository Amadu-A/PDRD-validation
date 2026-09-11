# services/document-service/src/pdrd_document_service/transport/http/schemas/pdf.py

"""HTTP schemas PDF extraction."""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_document_service.domain.pdf import (
    PdfPageType,
)


class PdfNormalizedBoundingBoxResponse(
    BaseModel,
):
    """Нормализованный bbox PDF text word."""

    model_config = ConfigDict(
        frozen=True,
    )

    x_min: int
    y_min: int
    x_max: int
    y_max: int


class PdfTextWordResponse(BaseModel):
    """Слово PDF с геометрией."""

    model_config = ConfigDict(
        frozen=True,
    )

    text: str

    bbox: PdfNormalizedBoundingBoxResponse

    block_no: int
    line_no: int
    word_no: int


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

    text_words: list[PdfTextWordResponse] = Field(
        default_factory=list,
    )


class ProjectContextTextPageResponse(
    BaseModel,
):
    """Text-only страница выбранного диапазона ПЗ."""

    model_config = ConfigDict(
        frozen=True,
    )

    page_number: int
    text: str


class ExplanatoryNoteContextResponse(
    BaseModel,
):
    """Извлечённый контекст Пояснительной записки."""

    model_config = ConfigDict(
        frozen=True,
    )

    enabled: bool

    start_page: int | None = None
    end_page: int | None = None

    pages_count: int = 0

    pages: list[ProjectContextTextPageResponse]


class PdfExtractionResponse(BaseModel):
    """Результат подготовки PDF."""

    model_config = ConfigDict(
        frozen=True,
    )

    file_name: str

    total_pages: int

    selected_pages: list[int]

    pages: list[PdfPageResponse]

    explanatory_note_context: ExplanatoryNoteContextResponse
