# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/normative_requirements.py

"""Внутреннее API поколений существующего нормативного каталога."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    NormativeRequirementsConflictError,
    NormativeRequirementsNotFoundError,
)
from pdrd_knowledge_service.core.container import ApplicationContainer
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.schemas.normative_requirements import (
    RecordNormativeStatusRequest,
)

router = APIRouter(
    prefix="/internal/v1/normative/documents", tags=["normative-catalog"]
)
Container = Annotated[ApplicationContainer, Depends(get_container)]


async def _call(awaitable):
    """Разделяет отсутствие источника, конфликт и ошибку команды; исключения кода не маскирует."""
    try:
        return await awaitable
    except NormativeRequirementsNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except NormativeRequirementsConflictError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def _catalog(container):
    """Сценарий создаётся composition root, а не HTTP-обработчиком."""
    if container.normative_requirements is None:
        raise HTTPException(503, "Атомарный нормативный каталог не настроен.")
    return container.normative_requirements


@router.post("/{document_id}/requirements/index", status_code=202)
async def queue_requirements(document_id: UUID, container: Container):
    """Явно индексирует только атомарный каталог, не удаляя рабочие чанки."""
    return await _call(_catalog(container).queue(document_id=document_id))


@router.get("/{document_id}/requirements/index")
async def requirement_index_state(document_id: UUID, container: Container):
    """Разделяет готовый chunk index и состояние нового атомарного каталога."""
    return await _call(_catalog(container).index_state(document_id=document_id))


@router.get("/{document_id}/requirements")
async def list_requirements(
    document_id: UUID,
    container: Container,
    generation_id: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    assessed_on: date | None = None,
):
    """Первая страница отдаёт generation_id, который закрепляется в дальнейшей пагинации."""
    return await _call(
        _catalog(container).list(
            document_id=document_id,
            generation_id=str(generation_id) if generation_id else None,
            offset=offset,
            limit=limit,
            assessed_on=assessed_on.isoformat() if assessed_on else None,
        )
    )


@router.get("/{document_id}/status-evidence")
async def list_status_evidence(document_id: UUID, container: Container):
    """Готовность индекса не подменяет отсутствие подтверждённого нормативного статуса."""
    return await _call(_catalog(container).evidence(document_id=document_id))


@router.post("/{document_id}/status-evidence", status_code=201)
async def record_status_evidence(
    document_id: UUID,
    payload: RecordNormativeStatusRequest,
    container: Container,
    actor_id: Annotated[UUID, Header(alias="X-PDRD-Actor-ID")],
):
    """Доверяет идентичности Gateway в изолированной внутренней сети."""
    return await _call(
        _catalog(container).record_status(
            document_id=document_id,
            payload=payload.model_dump(mode="json"),
            actor_id=actor_id,
        )
    )
