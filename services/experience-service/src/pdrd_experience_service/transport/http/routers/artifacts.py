# services/experience-service/src/pdrd_experience_service/transport/http/routers/artifacts.py

"""Закрытый реестр версий через Gateway; создание задания не выполняет embedding."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from pdrd_experience_service.core.container import ApplicationContainer
from pdrd_experience_service.transport.http.dependencies import (
    get_container,
    trusted_actor,
)
from pdrd_experience_service.transport.http.routers.catalog import catalog_errors
from pdrd_experience_service.transport.http.schemas.artifacts import (
    ChangeVersion,
    CreateVersion,
)

router = APIRouter(
    prefix="/internal/v1/experience-versions", tags=["experience-versions"]
)
Container = Annotated[ApplicationContainer, Depends(get_container)]
Actor = Annotated[str, Depends(trusted_actor)]


def connected(container: ApplicationContainer):
    """DI остаётся единственным местом создания репозитория."""
    if container.artifacts is None:
        raise HTTPException(503, "Реестр версий не подключён.")
    return container.artifacts


@router.get("")
async def versions(actor: Actor, container: Container) -> dict:
    """Выбранная для просмотра версия отделена от applied."""
    del actor
    with catalog_errors():
        return await connected(container).list()


@router.post("")
async def create(command: CreateVersion, actor: Actor, container: Container) -> dict:
    """Сохраняет выбранные редакции и долговечную задачу одной транзакцией."""
    with catalog_errors():
        return await connected(container).create(
            kind=command.kind,
            name=command.name,
            model=command.model,
            references=tuple((item.id, item.revision) for item in command.items),
            actor=actor,
        )


@router.get("/{version_id}")
async def version(version_id: UUID, actor: Actor, container: Container) -> dict:
    """Возвращает неизменяемый состав версии для подсветки и проверки."""
    del actor
    with catalog_errors():
        return await connected(container).get(version_id)


@router.patch("/{version_id}")
async def rename(
    version_id: UUID, command: ChangeVersion, actor: Actor, container: Container
) -> dict:
    """Редактируется только отображаемое название."""
    with catalog_errors():
        return await connected(container).rename(
            version_id=version_id,
            revision=command.expected_revision,
            name=command.name,
            actor=actor,
        )


@router.post("/{version_id}/delete")
async def delete(
    version_id: UUID, command: ChangeVersion, actor: Actor, container: Container
) -> dict:
    """Аудит и manifest сохраняются; Qdrant коллекция пока не удаляется физически."""
    with catalog_errors():
        return await connected(container).delete(
            version_id=version_id, revision=command.expected_revision, actor=actor
        )


@router.post("/{version_id}/apply")
async def apply(
    version_id: UUID, command: ChangeVersion, actor: Actor, container: Container
) -> dict:
    """Не применяет непроверенную базу или подготовленный датасет вместо модели."""
    with catalog_errors():
        return await connected(container).apply(
            version_id=version_id, revision=command.expected_revision, actor=actor
        )
