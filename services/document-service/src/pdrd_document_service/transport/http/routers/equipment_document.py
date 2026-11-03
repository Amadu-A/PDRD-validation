# services/document-service/src/pdrd_document_service/transport/http/routers/equipment_document.py

"""Внутренний HTTP API текстового извлечения документа производителя."""

import base64
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pdrd_document_service.core.container import ApplicationContainer
from pdrd_document_service.transport.http.dependencies import get_container

router = APIRouter(prefix="/internal/v1/equipment-documents", tags=["equipment"])


class EquipmentReportRequest(BaseModel):
    """Закреплённая сервером версия EQ и все позиции отчёта."""

    job_id: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=1)
    rows: list[dict] = Field(max_length=2000)


@router.post("/report-pdf")
async def export_equipment_pdf(
    payload: EquipmentReportRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> Response:
    """Создаёт самостоятельный PDF, не обращаясь к исходному проектному документу."""
    use_case = container.export_equipment_report
    if use_case is None:
        raise HTTPException(503, "Экспорт EQ не настроен.")
    content = use_case.execute(**payload.model_dump())
    return Response(content, media_type="application/pdf")


class EquipmentPageResponse(BaseModel):
    """Текст физической страницы документа производителя."""

    page: int
    text: str


class EquipmentDocumentResponse(BaseModel):
    """Результат ограниченного детерминированного извлечения."""

    media_type: str
    total_pages: int
    pages: list[EquipmentPageResponse]


@router.post("/extract", response_model=EquipmentDocumentResponse)
async def extract_equipment_document(
    container: Annotated[ApplicationContainer, Depends(get_container)],
    file: Annotated[UploadFile, File()],
    media_type: Annotated[str, Form()],
) -> EquipmentDocumentResponse:
    """Возвращает PDF/HTML текст для проверки применимости и EQ Facts."""
    use_case = container.extract_equipment_document
    if use_case is None:
        raise HTTPException(503, "Извлечение документации оборудования не настроено.")
    try:
        content = await file.read(use_case.max_bytes + 1)
        result = use_case.execute(content, media_type)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    finally:
        await file.close()
    return EquipmentDocumentResponse(
        media_type=result.media_type,
        total_pages=result.total_pages,
        pages=[
            EquipmentPageResponse(page=page.page, text=page.text)
            for page in result.pages
        ],
    )


@router.post("/render-page")
async def render_equipment_page(
    container: Annotated[ApplicationContainer, Depends(get_container)],
    file: Annotated[UploadFile, File()],
    page_number: Annotated[int, Form()],
) -> dict[str, int | str]:
    """Отдаёт один ограниченный растр для адресного анализа скана."""
    use_case = container.extract_equipment_document
    if use_case is None:
        raise HTTPException(503, "Обработка документации оборудования не настроена.")
    try:
        content = await file.read(use_case.max_bytes + 1)
        if not content or len(content) > use_case.max_bytes:
            raise ValueError("Размер документа производителя недопустим.")
        image = use_case.reader.render_page(content, page_number, use_case.max_pages)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    finally:
        await file.close()
    return {
        "page": page_number,
        "image_base64": base64.b64encode(image).decode("ascii"),
    }
