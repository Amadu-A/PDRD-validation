# services/document-service/src/pdrd_document_service/transport/http/schemas/combined.py

"""HTTP schemas объединённой PDF + CAD подготовки."""

from pydantic import BaseModel, ConfigDict

from pdrd_document_service.transport.http.schemas.cad import (
    CadExtractionResponse,
)
from pdrd_document_service.transport.http.schemas.pdf import (
    ExplanatoryNoteContextResponse,
    PdfPageResponse,
)


class CombinedExtractionResponse(BaseModel):
    """Результат подготовки соответствующих PDF и CAD листов."""

    model_config = ConfigDict(
        frozen=True,
    )

    pdf_file_name: str
    cad_file_name: str

    total_pdf_pages: int
    selected_page: int

    analysis_text: str

    pdf: PdfPageResponse
    cad: CadExtractionResponse

    combined_image_base64: str

    explanatory_note_context: ExplanatoryNoteContextResponse
