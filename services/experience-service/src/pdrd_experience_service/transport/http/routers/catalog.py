# services/experience-service/src/pdrd_experience_service/transport/http/routers/catalog.py

"""Закрытый CRUD и переносимый экспорт постоянного Experience для Gateway."""

from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from pdrd_experience_service.application.ports.analysis_source import (
    AnalysisSourceUnavailableError,
)
from pdrd_experience_service.core.container import ApplicationContainer
from pdrd_experience_service.domain.catalog import CatalogFilter
from pdrd_experience_service.domain.review import (
    ReviewConflictError,
    ReviewError,
    ReviewNotReadyError,
)
from pdrd_experience_service.transport.http.catalog_views import entry_view
from pdrd_experience_service.transport.http.dependencies import (
    get_container,
    trusted_actor,
)
from pdrd_experience_service.transport.http.schemas.catalog import (
    CaptureCommand,
    CatalogQuery,
    CatalogUpdate,
)

router = APIRouter(prefix="/internal/v1/experience", tags=["experience-internal"])
Container = Annotated[ApplicationContainer, Depends(get_container)]
Actor = Annotated[str, Depends(trusted_actor)]
Filters = Annotated[CatalogQuery, Query()]


@contextmanager
def catalog_errors():
    """Не раскрывает пути PNG, внутренние HTTP адреса и детали SQL."""
    try:
        yield
    except LookupError as error:
        raise HTTPException(404, "Пример или Review не найден.") from error
    except (ReviewConflictError, ReviewNotReadyError) as error:
        raise HTTPException(
            409, "Редакция изменена или Review ещё не утверждён."
        ) from error
    except ReviewError as error:
        raise HTTPException(422, str(error)) from error
    except (OSError, RuntimeError, AnalysisSourceUnavailableError) as error:
        raise HTTPException(
            503, "Каталог или crop временно недоступен; повторите операцию."
        ) from error


def connected(container):
    """Проверяет внедрение зависимостей до выполнения сценария."""
    if container.catalog is None or container.export_catalog is None:
        raise HTTPException(503, "Каталог Experience не подключён.")
    return container.catalog


@router.get("")
async def list_examples(filters: Filters, actor: Actor, container: Container) -> dict:
    """Не выдаёт демонстрационные строки за настоящую базу данных."""
    del actor
    with catalog_errors():
        entries, total = await connected(container).list(
            CatalogFilter(**filters.model_dump())
        )
    return {
        "items": [entry_view(entry) for entry in entries],
        "total": total,
        "offset": filters.offset,
        "limit": filters.limit,
    }


@router.get("/export")
async def export_examples(
    filters: Filters, actor: Actor, container: Container
) -> Response:
    """ZIP включает полный отфильтрованный набор, независимо от страницы UI."""
    del actor
    connected(container)
    with catalog_errors():
        content = await container.export_catalog.execute(
            CatalogFilter(**filters.model_dump())
        )
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="experience.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/capture/{job_id}")
async def capture_examples(
    job_id: UUID, command: CaptureCommand, actor: Actor, container: Container
) -> dict:
    """Повторное сохранение утверждённого задания безопасно после сбоя crop."""
    if container.capture_experience is None:
        raise HTTPException(503, "Сохранение Experience не подключено.")
    with catalog_errors():
        return await container.capture_experience.execute(
            job_id=job_id, expected_revision=command.expected_revision, actor=actor
        )


@router.get("/{example_id}")
async def get_example(example_id: UUID, actor: Actor, container: Container) -> dict:
    """Актуальность источника проверяется при каждом чтении."""
    del actor
    with catalog_errors():
        return entry_view(await connected(container).get(example_id))


@router.patch("/{example_id}")
async def update_example(
    example_id: UUID, command: CatalogUpdate, actor: Actor, container: Container
) -> dict:
    """Редакция и аудит сохраняются с CAS; теги и оригиналы не принимаются."""
    with catalog_errors():
        return entry_view(
            await connected(container).update(
                example_id=example_id,
                expected_revision=command.expected_revision,
                fields=command.fields.model_dump(exclude_unset=True),
                actor=actor,
            )
        )


@router.delete("/{example_id}")
async def deactivate_example(
    example_id: UUID,
    actor: Actor,
    container: Container,
    expected_revision: Annotated[int, Query(ge=0)],
) -> dict:
    """Удаление из активного набора сохраняет источник и всю историю в базе."""
    with catalog_errors():
        return entry_view(
            await connected(container).update(
                example_id=example_id,
                expected_revision=expected_revision,
                fields={"active": False},
                actor=actor,
            )
        )


@router.get("/{example_id}/history")
async def example_history(example_id: UUID, actor: Actor, container: Container) -> dict:
    """Полный журнал редакций отдельного примера."""
    del actor
    with catalog_errors():
        return {"events": await connected(container).history(example_id)}


@router.get("/{example_id}/crops/{index}")
async def example_crop(
    example_id: UUID, index: int, actor: Actor, container: Container
) -> Response:
    """Выдаёт только проверенные PNG своего примера, без произвольных путей."""
    del actor
    with catalog_errors():
        content = await connected(container).image(example_id=example_id, index=index)
    return Response(
        content, media_type="image/png", headers={"Cache-Control": "no-store"}
    )
