# services/experience-service/src/pdrd_experience_service/transport/http/routers/artifact_dataset.py

"""Закрытая выгрузка фиксированного набора: права субъекта проверяет Gateway."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from pdrd_experience_service.core.container import ApplicationContainer
from pdrd_experience_service.transport.http.dependencies import (
    get_container,
    trusted_actor,
)
from pdrd_experience_service.transport.http.routers.catalog import catalog_errors

router = APIRouter(
    prefix="/internal/v1/experience-versions", tags=["experience-versions"]
)
Container = Annotated[ApplicationContainer, Depends(get_container)]
Actor = Annotated[str, Depends(trusted_actor)]


@router.get("/{version_id}/dataset")
async def dataset(version_id: UUID, actor: Actor, container: Container) -> Response:
    """Подготовленный ZIP не запускает обучение и не зависит от полей текущего каталога."""
    del actor
    if container.export_artifact_dataset is None:
        raise HTTPException(503, "Выгрузка фиксированных наборов не подключена.")
    with catalog_errors():
        content = await container.export_artifact_dataset.execute(version_id)
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="experience-dataset-{version_id}.zip"',
            "Cache-Control": "no-store",
        },
    )
