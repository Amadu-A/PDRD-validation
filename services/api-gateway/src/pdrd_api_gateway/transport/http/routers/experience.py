# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/experience.py

"""Experience через тот же закрытый frontend-канал и отдельный application use case."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.request_actor import authenticated_actor
from pdrd_api_gateway.transport.http.routers.review import require_review_channel
from pdrd_api_gateway.transport.http.schemas.experience import (
    DeleteSelection,
    ExperienceQuery,
    ExperienceUpdate,
)
from pdrd_api_gateway.transport.http.schemas.review import StrictCommand

router = APIRouter(prefix="/api/v1/experience", tags=["experience"])
Container = Annotated[ApplicationContainer, Depends(require_review_channel)]
Filters = Annotated[ExperienceQuery, Query()]


async def invoke(container: ApplicationContainer, request: Request, **options):
    """Безопасные ошибки use case сохраняют свои статусы HTTP."""
    if container.manage_experience is None:
        raise HTTPException(503, "Каталог Experience не подключён.")
    try:
        return await container.manage_experience.execute(
            **options, actor=authenticated_actor(request)
        )
    except ReviewRequestError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.get("")
async def examples(filters: Filters, request: Request, container: Container) -> dict:
    """Gateway не выполняет поиск по таблицам чужого сервиса."""
    return await invoke(
        container,
        request,
        operation="list",
        job_id=filters.job_id,
        query=filters.model_dump(mode="json", exclude_none=True),
    )


@router.get("/export")
async def export_examples(
    filters: Filters, request: Request, container: Container
) -> Response:
    """Не кеширует архив и не переносит внутренние заголовки служебного клиента."""
    content = await invoke(
        container,
        request,
        operation="export",
        job_id=filters.job_id,
        query=filters.model_dump(mode="json", exclude_none=True),
    )
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="experience.zip"',
        },
    )


@router.post("/capture/{job_id}")
async def capture_examples(
    job_id: UUID, command: StrictCommand, request: Request, container: Container
) -> dict:
    """Браузер сообщает только ожидаемую утверждённую ревизию."""
    return await invoke(
        container,
        request,
        operation="capture",
        job_id=job_id,
        command=command.model_dump(),
    )


@router.get("/{example_id}")
async def example(example_id: UUID, request: Request, container: Container) -> dict:
    """Проверяет задание примера через application policy."""
    return await invoke(container, request, operation="read", example_id=example_id)


@router.post("/delete-selection")
async def delete_selection(
    command: DeleteSelection, request: Request, container: Container
) -> dict:
    """Один запрос сохраняет атомарное удаление всех выбранных строк."""
    return await invoke(
        container,
        request,
        operation="delete_selection",
        command=command.model_dump(mode="json"),
    )


@router.patch("/{example_id}")
async def edit_example(
    example_id: UUID, command: ExperienceUpdate, request: Request, container: Container
) -> dict:
    """Задание, происхождение, tag/decision и оригинальный текст не принимаются."""
    return await invoke(
        container,
        request,
        operation="curate",
        example_id=example_id,
        command=command.model_dump(exclude_unset=True),
    )


@router.delete("/{example_id}")
async def deactivate_example(
    example_id: UUID,
    request: Request,
    container: Container,
    expected_revision: Annotated[int, Query(ge=0)],
) -> dict:
    """Деактивация сохраняет историю и позволяет восстановить активность."""
    return await invoke(
        container,
        request,
        operation="deactivate",
        example_id=example_id,
        query={"expected_revision": expected_revision},
    )


@router.get("/{example_id}/history")
async def history(example_id: UUID, request: Request, container: Container) -> dict:
    """Журнал инженерных редакций доступен в том же закрытом канале."""
    return await invoke(container, request, operation="history", example_id=example_id)


@router.get("/{example_id}/crops/{index}")
async def image(
    example_id: UUID, index: int, request: Request, container: Container
) -> Response:
    """Браузер не может указать произвольный путь crop."""
    content = await invoke(
        container, request, operation="crop", example_id=example_id, index=index
    )
    return Response(
        content, media_type="image/png", headers={"Cache-Control": "no-store"}
    )
