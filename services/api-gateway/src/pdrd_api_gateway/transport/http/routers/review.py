# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/review.py

"""Контракт Review через закрытый frontend-канал с серверным контекстом.

Произвольный X-Review-Actor не пересылается. Ключ nginx не является
пользовательским credential и не возвращается в ответах.
"""

import secrets
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.dependencies import get_container
from pdrd_api_gateway.transport.http.schemas.review import ReviewCommand, StrictCommand

router = APIRouter(prefix="/api/v1", tags=["review"])


def review_available(request: Request, container: ApplicationContainer) -> bool:
    """Разрешает канал только при включённом режиме и правильном ключе nginx."""
    settings = container.settings.review
    key = settings.ui_key.get_secret_value()
    return (
        settings.enabled
        and bool(key)
        and secrets.compare_digest(
            request.headers.get("x-pdrd-review-key", "").encode("utf-8"),
            key.encode("utf-8"),
        )
    )


def require_review_channel(
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> ApplicationContainer:
    """Закрывает чтение/запись Review и cross-site команды браузера."""
    if not review_available(request, container):
        raise HTTPException(403, "Human Review доступен только через закрытый фронт.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Запрос с другого сайта запрещён.")
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
        raise HTTPException(403, "Источник запроса не соответствует закрытому фронту.")
    if container.manage_review is None:
        raise HTTPException(503, "Review не подключён.")
    return container


@router.get("/review/config")
async def review_config(
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Выбирает серверный режим без передачи ключа или actor в JavaScript."""
    return {"enabled": review_available(request, container)}


async def invoke(
    container: ApplicationContainer, job_id: UUID, operation: str, command=None
) -> dict:
    """Переводит ожидаемые application ошибки в HTTP."""
    try:
        return await container.manage_review.execute(
            job_id=job_id, operation=operation, command=command
        )
    except ReviewRequestError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.post("/analyses/{job_id}/review/open")
async def open_review(
    job_id: UUID,
    container: Annotated[ApplicationContainer, Depends(require_review_channel)],
) -> dict:
    """Открывает или восстанавливает Review завершённого задания."""
    return await invoke(container, job_id, "open")


@router.get("/analyses/{job_id}/review")
async def get_review(
    job_id: UUID,
    container: Annotated[ApplicationContainer, Depends(require_review_channel)],
) -> dict:
    """Читает актуальную ревизию для восстановления и обработки конфликта."""
    return await invoke(container, job_id, "read")


@router.post("/analyses/{job_id}/review/commands")
async def command_review(
    job_id: UUID,
    command: ReviewCommand,
    container: Annotated[ApplicationContainer, Depends(require_review_channel)],
) -> dict:
    """Передаёт строгую команду отдельно от доверенной серверной идентичности."""
    return await invoke(container, job_id, "command", command.model_dump())


@router.post("/analyses/{job_id}/reviewed-pdf")
async def reviewed_pdf(
    job_id: UUID,
    command: StrictCommand,
    container: Annotated[ApplicationContainer, Depends(require_review_channel)],
) -> Response:
    """Выдаёт PDF только текущей утверждённой редакции через закрытый канал."""
    if container.get_reviewed_pdf is None:
        raise HTTPException(503, "Экспорт итогового PDF не подключён.")
    try:
        document = await container.get_reviewed_pdf.execute(
            job_id=job_id,
            expected_revision=command.expected_revision,
        )
    except ReviewRequestError as error:
        raise HTTPException(error.status_code, error.detail) from error
    return Response(
        content=document.content,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "attachment; filename=\"reviewed.pdf\"; filename*=UTF-8''"
            + quote(document.file_name, safe=""),
        },
    )
