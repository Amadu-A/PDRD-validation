# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/normative_protocol.py

"""Ограниченный внутренний upload и авторизованное постраничное чтение аудита N-first."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pdrd_api_gateway.application.ports.normative_protocol import (
    NormativeProtocolError,
    NormativeProtocolNotFound,
)
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.analysis_access import enforce_analysis_job_access
from pdrd_api_gateway.transport.http.dependencies import get_container

router = APIRouter(tags=["normative-protocol"])
Container = Annotated[ApplicationContainer, Depends(get_container)]
ProtocolId = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
Collection = Literal[
    "manifest",
    "checks",
    "selection",
    "sources",
    "nodes",
    "page_metrics",
    "stage_records",
]


class ProtocolManifest(BaseModel):
    """Проверяемые размеры и scope; содержимое проверяется перед публикацией."""

    part_count: int = Field(ge=1, le=100000)
    bytes: int = Field(ge=1)
    section_id: UUID
    document_ids: list[UUID]
    counts: dict[str, int]


def _manager(container):
    """Отсутствующий DI закрывает операцию, не возвращает фиктивный успех."""
    if container.normative_protocol is None:
        raise HTTPException(503, "Хранилище нормативного протокола не настроено.")
    return container.normative_protocol


def _error(error):
    """Отсутствующий результат отличает от повреждённого и отменённого."""
    return HTTPException(
        404
        if isinstance(error, NormativeProtocolNotFound)
        else 409
        if str(error) == "analysis_cancelled"
        else 503,
        str(error),
    )


@router.put(
    "/internal/v1/analysis-artifacts/{document_id}/normative-protocols/{protocol_id}/parts/{index}"
)
async def put_part(
    document_id: UUID,
    protocol_id: ProtocolId,
    index: int,
    request: Request,
    container: Container,
):
    """Принимает не более 256 KiB, включая chunked HTTP без Content-Length."""
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 256 * 1024:
            raise HTTPException(413, "Часть протокола превышает 256 KiB.")
    try:
        await _manager(container).store.put_part(
            document_id, protocol_id, index, bytes(content)
        )
    except (NormativeProtocolError, NormativeProtocolNotFound) as error:
        raise _error(error) from error
    return {"saved": True}


@router.post(
    "/internal/v1/analysis-artifacts/{document_id}/normative-protocols/{protocol_id}/publish"
)
async def publish(
    document_id: UUID,
    protocol_id: ProtocolId,
    payload: ProtocolManifest,
    container: Container,
):
    """Публикует только проверенный полный архив настоящего задания."""
    try:
        return await _manager(container).publish(
            document_id, protocol_id, payload.model_dump(mode="json")
        )
    except (NormativeProtocolError, NormativeProtocolNotFound) as error:
        raise _error(error) from error


async def _authorize(request, container):
    """Сохраняет владельца/guest capability даже при выключенном auth-профиле разработки."""
    if not container.settings.identity_proxy.authorization_enabled:
        denial = await enforce_analysis_job_access(
            request, container.get_analysis_job, None
        )
        if denial is not None:
            raise HTTPException(denial.status_code, "Задание недоступно.")


@router.get("/api/v1/analyses/{job_id}/normative-protocols/{protocol_id}")
async def read_protocol(
    job_id: UUID,
    protocol_id: ProtocolId,
    request: Request,
    response: Response,
    container: Container,
    collection: Collection = "manifest",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
):
    """Никогда не принимает document_id клиента вместо job_id."""
    await _authorize(request, container)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _manager(container).read(
            job_id, protocol_id, collection, offset, limit
        )
    except (NormativeProtocolError, NormativeProtocolNotFound) as error:
        raise _error(error) from error


@router.get("/api/v1/analyses/{job_id}/normative-protocols/{protocol_id}/parts/{index}")
async def read_part(
    job_id: UUID,
    protocol_id: ProtocolId,
    index: int,
    request: Request,
    container: Container,
):
    """Позволяет lossless-экспорт архива ограниченными частями, включая большие атомы."""
    await _authorize(request, container)
    manager = _manager(container)
    try:
        manifest = await manager.read(job_id, protocol_id, "manifest", 0, 1)
        content = await manager.store.read_part(
            UUID(manifest["document_id"]), protocol_id, index
        )
        return Response(
            content,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )
    except (NormativeProtocolError, NormativeProtocolNotFound) as error:
        raise _error(error) from error


@router.get(
    "/internal/v1/analysis-artifacts/{document_id}/normative-protocols/{protocol_id}"
)
async def read_internal(
    document_id: UUID,
    protocol_id: ProtocolId,
    container: Container,
    collection: Collection = "stage_records",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
):
    """Возвращает n8n опубликованные записи ограниченными страницами."""
    try:
        return await _manager(container).store.read(
            document_id, protocol_id, collection, offset, limit
        )
    except (NormativeProtocolError, NormativeProtocolNotFound) as error:
        raise _error(error) from error


@router.get(
    "/internal/v1/analysis-artifacts/{document_id}/normative-protocols/{protocol_id}/stage-stream"
)
async def read_stage_stream(
    document_id: UUID,
    protocol_id: ProtocolId,
    container: Container,
    offset: int = Query(0, ge=0),
):
    """Пагинация HTTP Request n8n: каждый ответ ограничен 256 KiB исходных bytes."""
    try:
        return await _manager(container).store.stream(document_id, protocol_id, offset)
    except (NormativeProtocolError, NormativeProtocolNotFound) as error:
        raise _error(error) from error
