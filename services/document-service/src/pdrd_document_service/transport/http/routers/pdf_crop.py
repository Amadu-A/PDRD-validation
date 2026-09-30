# services/document-service/src/pdrd_document_service/transport/http/routers/pdf_crop.py

"""Внутреннее API crop: бинарный PDF и строгие нормализованные области."""

import base64
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pdrd_document_service.core.container import ApplicationContainer
from pdrd_document_service.domain.pdf import PdfNormalizedBoundingBox
from pdrd_document_service.transport.http.dependencies import get_container

router = APIRouter(prefix="/internal/v1/pdf", tags=["pdf-crop"])


class Box(BaseModel):
    """Дробные конечные координаты без дополнительных полей."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    x_min: float = Field(ge=0, le=1000)
    y_min: float = Field(ge=0, le=1000)
    x_max: float = Field(ge=0, le=1000)
    y_max: float = Field(ge=0, le=1000)


class CropSpec(BaseModel):
    """Один физический лист и ограниченное число областей."""

    model_config = ConfigDict(extra="forbid", strict=True)
    page_number: int = Field(ge=1)
    regions: list[Box] = Field(min_length=1, max_length=4)


@router.post("/crop")
async def crop_pdf(
    file: Annotated[UploadFile, File()],
    specification: Annotated[str, Form()],
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Передаёт PNG только внутреннему клиенту; не хранит исходный документ."""
    if container.crop_pdf is None:
        raise HTTPException(503, "Вырезание областей не подключено.")
    if len(specification) > 5000:
        raise HTTPException(422, "Некорректный запрос crop.")
    try:
        spec = CropSpec.model_validate_json(specification)
        content = await file.read(container.settings.pdf.max_upload_bytes + 1)
        images = container.crop_pdf.execute(
            content=content,
            page_number=spec.page_number,
            regions=tuple(
                PdfNormalizedBoundingBox(**box.model_dump()) for box in spec.regions
            ),
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(
            422, "Проверьте PDF, номер листа и области crop."
        ) from error
    finally:
        await file.close()
    return {"images": [base64.b64encode(content).decode("ascii") for content in images]}
