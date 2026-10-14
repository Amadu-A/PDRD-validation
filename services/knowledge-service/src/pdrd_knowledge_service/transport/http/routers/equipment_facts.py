# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/equipment_facts.py

"""Внутренние контракты извлечения и чтения EQ Facts."""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from pdrd_knowledge_service.core.container import ApplicationContainer
from pdrd_knowledge_service.transport.http.dependencies import get_container

router = APIRouter(prefix="/internal/v1/equipment-facts", tags=["equipment-facts"])


class EquipmentPageInput(BaseModel):
    """Ограниченный текст одной страницы документа производителя."""

    page: int = Field(ge=1)
    text: str = Field(max_length=20_000)


class EquipmentVisionFactInput(BaseModel):
    """Ограниченная транскрипция строки одной визуально проверенной страницы."""

    page: int = Field(ge=1, le=40)
    property_name: str = Field(max_length=300)
    value_raw: str = Field(max_length=300)
    unit_raw: str = Field(max_length=300)
    role: str = Field(default="", max_length=30)
    current_type: str = Field(default="", max_length=30)
    phase: str = Field(default="", max_length=30)
    snippet: str = Field(min_length=1, max_length=300)


class EquipmentFactsRequest(BaseModel):
    """Неизменяемый документ и модель для EQ Facts."""

    source_id: str = Field(min_length=3, max_length=80)
    sha256: str = Field(min_length=64, max_length=64)
    manufacturer: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    variant: str = Field(default="", max_length=200)
    properties: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    pages: list[EquipmentPageInput] = Field(max_length=40)
    vision_facts: list[EquipmentVisionFactInput] = Field(
        default_factory=list, max_length=16
    )


@router.post("/extract")
async def extract_equipment(
    payload: EquipmentFactsRequest,
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict[str, Any]:
    """Извлекает или читает кэш только при корректной версии snapshot."""
    if not re.fullmatch(r"[0-9a-f]{64}", payload.sha256):
        raise HTTPException(422, "Некорректный SHA-256 документа.")
    index = container.equipment_fact_index
    if index is None:
        raise HTTPException(503, "Индекс EQ Facts недоступен.")
    try:
        return await index.extract_or_get(
            source_id=payload.source_id,
            sha256=payload.sha256,
            manufacturer=payload.manufacturer,
            model=payload.model,
            variant=payload.variant,
            properties=payload.properties,
            pages=[page.model_dump() for page in payload.pages],
            vision_facts=tuple(fact.model_dump() for fact in payload.vision_facts),
            should_stop=request.is_disconnected,
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/{source_id}")
async def get_equipment(
    source_id: str,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict[str, Any]:
    """Возвращает EQ Facts конкретного сохранённого документа."""
    index = container.equipment_fact_index
    if index is None:
        raise HTTPException(503, "Индекс EQ Facts недоступен.")
    result = await index.get(source_id)
    if result is None:
        raise HTTPException(404, "EQ Facts не найдены.")
    return result
