# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/equipment_search.py

"""Пользовательские EQ-результаты и внутренние callbacks оркестратора."""

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.equipment_updates import build_equipment_update
from pdrd_api_gateway.infrastructure.equipment_search import EquipmentSearchClient
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.identity_authorization import VerifiedIdentity
from pdrd_api_gateway.transport.http.request_actor import authenticated_actor
from pdrd_api_gateway.transport.http.routers.review import (
    invoke,
    require_review_channel,
)

router = APIRouter(tags=["equipment-search"])


class StartEquipmentRequest(BaseModel):
    """Подтверждённые модели из page Understanding."""

    identities: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    allow_unverified: bool = False


class SourceUpdateRequest(BaseModel):
    """Решение о доверии точному домену."""

    manufacturer: str = Field(min_length=1, max_length=200)
    hostname: str = Field(min_length=3, max_length=253)
    status: str
    enabled: bool = True
    allow_http: bool = False
    reason: str = Field(min_length=1, max_length=1000)


class ResumeEquipmentRequest(BaseModel):
    """Ожидаемая версия сохраняемого EQ состояния."""

    revision: int = Field(ge=1)


class ImportEquipmentRequest(BaseModel):
    """Версии EQ и Review; содержание находок браузер не задаёт."""

    model_config = {"extra": "forbid", "strict": True}
    revision: int = Field(ge=1)
    expected_revision: int = Field(ge=0)


def _client(container: ApplicationContainer) -> EquipmentSearchClient:
    """Создаёт клиент с ключом только из конфигурации сервера."""
    return EquipmentSearchClient(container.settings.equipment_search)


async def _document_id(container: ApplicationContainer, job_id: UUID) -> UUID:
    """Проверяет существование анализа и получает его document ID."""
    getter = container.get_analysis_job
    if getter is None:
        raise HTTPException(503, "Хранилище заданий недоступно.")
    job = await getter.execute(job_id=job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено.")
    return job.document_id


def _manager(request: Request) -> UUID:
    """Повторно проверяет серверное право даже без auth middleware."""
    identity = getattr(request.state, "verified_identity", None)
    if (
        not isinstance(identity, VerifiedIdentity)
        or "equipment_sources.manage" not in identity.permissions
    ):
        raise HTTPException(403, "Нет права управления EQ-источниками.")
    return identity.user_id


def _upstream_error(error: httpx.HTTPError) -> HTTPException:
    """Сохраняет 404 внутреннего сервиса, остальные ошибки скрывает."""
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code == 404:
            return HTTPException(404, "EQ-данные не найдены.")
        if error.response.status_code == 409:
            return HTTPException(
                409, "EQ версия изменилась или продолжение недоступно."
            )
    return HTTPException(503, "Equipment Search временно недоступен.")


@router.post("/internal/v1/equipment-search/{document_id}/start")
async def start_equipment(
    document_id: UUID,
    payload: StartEquipmentRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Запускает EQ параллельно основной ветви n8n."""
    try:
        return await _client(container).start(
            document_id,
            payload.identities,
            payload.allow_unverified,
        )
    except (httpx.HTTPError, RuntimeError, ValueError):
        return {
            "status": "incomplete",
            "results": [],
            "warning": "Equipment Search не удалось запустить.",
        }


@router.get("/internal/v1/equipment-search/{document_id}/wait")
async def wait_equipment(
    document_id: UUID,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Ждёт EQ в пределах бюджета и не роняет основной анализ."""
    return await _client(container).wait(document_id)


@router.get("/api/v1/analyses/{job_id}/equipment-search")
async def equipment_result(
    job_id: UUID,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Отдаёт состояние EQ только в области доступного анализа."""
    document_id = await _document_id(container, job_id)
    try:
        return await _client(container).state(document_id)
    except (httpx.HTTPError, RuntimeError) as error:
        if isinstance(error, httpx.HTTPError):
            raise _upstream_error(error) from error
        raise HTTPException(503, "Equipment Search не настроен.") from error


@router.get("/api/v1/analyses/{job_id}/equipment-search/report")
async def equipment_report(
    job_id: UUID,
    container: Annotated[ApplicationContainer, Depends(get_container)],
    revision: int | None = None,
) -> dict:
    """Читает отдельное дополнение; исходный отчёт и Review остаются сохранёнными."""
    await _document_id(container, job_id)
    try:
        return await build_equipment_update(container).execute(
            job_id=job_id, revision=revision
        )
    except ReviewRequestError as error:
        raise HTTPException(error.status_code, error.detail) from error
    except httpx.HTTPError as error:
        raise _upstream_error(error) from error


@router.post("/api/v1/analyses/{job_id}/review/equipment-updates")
async def import_equipment_report(
    job_id: UUID,
    payload: ImportEquipmentRequest,
    request: Request,
    container: Annotated[ApplicationContainer, Depends(require_review_channel)],
) -> dict:
    """Явно прикрепляет выбранную неизменяемую EQ-версию, не принимая находки браузера."""
    try:
        proposal = await build_equipment_update(container).execute(
            job_id=job_id, revision=payload.revision
        )
    except ReviewRequestError as error:
        raise HTTPException(error.status_code, error.detail) from error
    except httpx.HTTPError as error:
        raise _upstream_error(error) from error
    return await invoke(
        container,
        job_id,
        "equipment_update",
        {
            "expected_revision": payload.expected_revision,
            "equipment_revision": payload.revision,
            "findings": proposal["findings"],
        },
        actor=authenticated_actor(request),
    )


@router.post("/api/v1/analyses/{job_id}/equipment-search/cancel")
async def cancel_equipment(
    job_id: UUID, container: Annotated[ApplicationContainer, Depends(get_container)]
) -> dict:
    """Отменяет только фоновую EQ-ветвь доступного задания."""
    document_id = await _document_id(container, job_id)
    try:
        await _client(container).cancel(document_id)
        return await _client(container).state(document_id)
    except httpx.HTTPError as error:
        raise _upstream_error(error) from error


@router.post("/api/v1/analyses/{job_id}/equipment-search/resume")
async def resume_equipment(
    job_id: UUID,
    payload: ResumeEquipmentRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Продолжает неполную проверку, сохраняя готовые модели."""
    document_id = await _document_id(container, job_id)
    try:
        return await _client(container).resume(document_id, payload.revision)
    except httpx.HTTPError as error:
        raise _upstream_error(error) from error


@router.get("/api/v1/analyses/{job_id}/equipment-search/events")
async def equipment_events(
    job_id: UUID,
    container: Annotated[ApplicationContainer, Depends(get_container)],
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Проксирует SSE с восстановлением по Last-Event-ID."""
    document_id = await _document_id(container, job_id)
    client = _client(container)
    try:
        await client.state(document_id)
    except (httpx.HTTPError, RuntimeError) as error:
        if isinstance(error, httpx.HTTPError):
            raise _upstream_error(error) from error
        raise HTTPException(503, "Equipment Search не настроен.") from error

    async def stream() -> AsyncIterator[bytes]:
        headers = client._headers()
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(None, connect=10),
                trust_env=False,
            ) as http,
            http.stream(
                "GET",
                f"{client._base()}/jobs/{document_id}/events",
                headers=headers,
            ) as response,
        ):
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/analyses/{job_id}/equipment-documents/{source_id}")
async def equipment_document(
    job_id: UUID,
    source_id: str,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> Response:
    """Отдаёт только snapshot, фактически использованный доступным заданием."""
    document_id = await _document_id(container, job_id)
    client = _client(container)
    try:
        state = await client.state(document_id)
        assigned = any(
            isinstance(result, dict)
            and isinstance(result.get("document"), dict)
            and result["document"].get("source_id") == source_id
            for result in state.get("results", [])
        )
        if not assigned:
            raise HTTPException(404, "EQ-документ не найден в задании.")
        content, media_type = await client.document(source_id)
    except httpx.HTTPError as error:
        raise _upstream_error(error) from error
    except RuntimeError as error:
        raise HTTPException(503, "Equipment Search не настроен.") from error
    if media_type not in {"application/pdf", "text/html"}:
        raise HTTPException(503, "Неизвестный формат документа.")
    suffix = ".pdf" if media_type == "application/pdf" else ".html"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=equipment-{source_id}{suffix}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get("/api/v1/equipment-sources")
async def sources(
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
    source_status: str | None = None,
    query: str = "",
) -> list[dict]:
    """Возвращает реестр только пользователю с правом управления."""
    _manager(request)
    try:
        return await _client(container).sources(status=source_status, query=query)
    except (httpx.HTTPError, RuntimeError) as error:
        raise HTTPException(503, "Каталог EQ-источников недоступен.") from error


@router.get("/api/v1/equipment-sources/manufacturers")
async def manufacturers(
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> list[dict]:
    """Показывает известных производителей и aliases."""
    _manager(request)
    try:
        return await _client(container).manufacturers()
    except (httpx.HTTPError, RuntimeError) as error:
        raise HTTPException(503, "Каталог EQ-источников недоступен.") from error


@router.put("/api/v1/equipment-sources")
async def update_source(
    request: Request,
    payload: SourceUpdateRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Сохраняет решение с инициатором из проверенной сессии."""
    actor = _manager(request)
    try:
        return await _client(container).update_source(payload.model_dump(), actor)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 422:
            raise HTTPException(422, "Некорректный источник.") from error
        raise HTTPException(503, "Каталог EQ-источников недоступен.") from error
    except (httpx.HTTPError, RuntimeError) as error:
        raise HTTPException(503, "Каталог EQ-источников недоступен.") from error


@router.get("/api/v1/equipment-sources/{source_id}/audit")
async def source_audit(
    request: Request,
    source_id: UUID,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> list[dict]:
    """Читает историю доверия и небезопасного транспорта."""
    _manager(request)
    try:
        return await _client(container).source_audit(source_id)
    except (httpx.HTTPError, RuntimeError) as error:
        raise HTTPException(503, "Каталог EQ-источников недоступен.") from error
