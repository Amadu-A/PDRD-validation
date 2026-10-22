# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/normative_requirements.py

"""Атомарный каталог в существующей области прав и доступа к нормативным разделам."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogManagementError,
)
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.routers.normative_catalog import (
    _require_facade,
    _translate_error,
)
from pdrd_api_gateway.transport.http.schemas.normative_requirements import (
    RecordNormativeStatusRequest,
)
from pdrd_api_gateway.transport.http.section_access import (
    enforce_normative_section_access,
)

router = APIRouter(
    prefix="/api/v1/normative/documents",
    tags=["normative-catalog"],
    dependencies=[Depends(enforce_normative_section_access)],
)
readiness_router = APIRouter(
    prefix="/api/v1/normative/sections",
    tags=["normative-catalog"],
    dependencies=[Depends(enforce_normative_section_access)],
)
Container = Annotated[ApplicationContainer, Depends(get_container)]


async def _call(awaitable):
    """Преобразует только прикладные ошибки порта, не скрывая программные исключения."""
    try:
        return await awaitable
    except NormativeCatalogManagementError as error:
        raise _translate_error(error) from error


@readiness_router.get("/{section_id}/atomic-readiness")
async def atomic_readiness(section_id: UUID, container: Container):
    """Сохраняет существующую проверку доступа к назначенному разделу."""
    return await _call(
        _require_facade(container).atomic_readiness(section_id=section_id)
    )


@router.get("/{document_id}/requirements")
async def list_requirements(
    document_id: UUID,
    container: Container,
    generation_id: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    assessed_on: date | None = None,
):
    """Сохраняет параметры неизменяемой пагинации и полную нормативную цитату."""
    query = {"offset": offset, "limit": limit}
    if generation_id:
        query["generation_id"] = str(generation_id)
    if assessed_on:
        query["assessed_on"] = assessed_on.isoformat()
    return await _call(
        _require_facade(container).list_requirements(
            document_id=document_id, query=query
        )
    )


@router.post("/{document_id}/requirements/index", status_code=202)
async def queue_requirements(document_id: UUID, container: Container):
    """Право normative.write проверяет общий IdentityAuthorizer."""
    return await _call(
        _require_facade(container).queue_requirements(document_id=document_id)
    )


@router.get("/{document_id}/requirements/index")
async def requirement_index_state(document_id: UUID, container: Container):
    """Проверяет состояние без создания новых очередей или изменений рабочего корпуса."""
    return await _call(
        _require_facade(container).requirement_index_state(document_id=document_id)
    )


@router.get("/{document_id}/status-evidence")
async def list_status_evidence(document_id: UUID, container: Container):
    """Выдаёт цитату подтверждающего источника, её область и дату, отдельно от READY."""
    return await _call(
        _require_facade(container).status_evidence(document_id=document_id)
    )


@router.post("/{document_id}/status-evidence", status_code=201)
async def record_status_evidence(
    document_id: UUID,
    payload: RecordNormativeStatusRequest,
    request: Request,
    container: Container,
):
    """Не разрешает клиенту подставить автора подтверждения или записать статус без сессии."""
    identity = getattr(request.state, "verified_identity", None)
    if identity is None:
        raise HTTPException(
            401, "Для аттестации нормативного статуса необходима авторизация."
        )
    if "normative.write" not in identity.permissions:
        raise HTTPException(403, "Нет права управления нормативными документами.")
    return await _call(
        _require_facade(container).record_status_evidence(
            document_id=document_id,
            payload=payload.model_dump(mode="json"),
            actor_id=identity.user_id,
        )
    )
