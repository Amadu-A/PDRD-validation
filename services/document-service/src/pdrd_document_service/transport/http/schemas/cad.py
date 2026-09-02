# services/document-service/src/pdrd_document_service/transport/http/schemas/cad.py

"""HTTP schemas CAD extraction."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from pdrd_document_service.domain.cad import CadFormat


class CadExtractionResponse(BaseModel):
    """Результат подготовки DWG/DXF."""

    model_config = ConfigDict(
        frozen=True,
    )

    original_file_name: str
    original_format: CadFormat
    normalized_format: CadFormat

    converted_from_dwg: bool
    selected_layout: str

    warnings: list[str]

    machine_data: dict[str, Any]
    machine_context: str

    image_base64: str
