# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/experience_versions.py

"""Просмотр/редактирование версий и отдельное применение в закрытом Review канале."""

from uuid import UUID

from fastapi import APIRouter, Response

from pdrd_api_gateway.transport.http.routers.experience import Container, invoke
from pdrd_api_gateway.transport.http.schemas.experience_versions import (
    ChangeVersion,
    CreateVersion,
)

router = APIRouter(prefix="/api/v1/experience-versions", tags=["experience-versions"])


@router.get("")
async def versions(container: Container) -> dict:
    """Просмотр никогда не меняет рабочую конфигурацию."""
    return await invoke(container, operation="version_list")


@router.post("")
async def create(command: CreateVersion, container: Container) -> dict:
    """Выбор сохраняется как долговечное задание; HTTP не ждёт embedding."""
    return await invoke(
        container, operation="version_create", command=command.model_dump(mode="json")
    )


@router.get("/{version_id}")
async def version(version_id: UUID, container: Container) -> dict:
    """Состав используется для подсветки и выбора отсутствующих примеров."""
    return await invoke(container, operation="version_read", example_id=version_id)


@router.get("/{version_id}/dataset")
async def dataset(version_id: UUID, container: Container) -> Response:
    """Gateway проверяет задания всех frozen members до получения архива."""
    content = await invoke(container, operation="version_export", example_id=version_id)
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="experience-dataset-{version_id}.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.patch("/{version_id}")
async def rename(
    version_id: UUID, command: ChangeVersion, container: Container
) -> dict:
    """Переименование не перемещает данные между коллекциями."""
    return await invoke(
        container,
        operation="version_rename",
        example_id=version_id,
        command=command.model_dump(),
    )


@router.post("/{version_id}/delete")
async def delete(
    version_id: UUID, command: ChangeVersion, container: Container
) -> dict:
    """Удалённая версия исключается из селекторов с сохранением аудита."""
    return await invoke(
        container,
        operation="version_delete",
        example_id=version_id,
        command=command.model_dump(),
    )


@router.post("/{version_id}/apply")
async def apply(version_id: UUID, command: ChangeVersion, container: Container) -> dict:
    """Применение требует готового артефакта и одобренного качества."""
    return await invoke(
        container,
        operation="version_apply",
        example_id=version_id,
        command=command.model_dump(),
    )
