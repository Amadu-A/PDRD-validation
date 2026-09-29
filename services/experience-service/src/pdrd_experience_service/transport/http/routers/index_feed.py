# services/experience-service/src/pdrd_experience_service/transport/http/routers/index_feed.py

"""Закрытый канал чтения E для Knowledge, с отдельным ключом без прав изменения Review."""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from pdrd_experience_service.core.container import ApplicationContainer
from pdrd_experience_service.domain.review import ReviewConflictError, ReviewError
from pdrd_experience_service.transport.http.dependencies import get_container

Container = Annotated[ApplicationContainer, Depends(get_container)]


def require_index_reader(request: Request, container: Container) -> None:
    """Допускает только служебный канал; actor инженера и ключ Review здесь не используются."""
    token = container.settings.index_key.get_secret_value()
    if not token or not secrets.compare_digest(
        request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()
    ):
        raise HTTPException(403, "Доступ разрешён только индексатору Experience.")
    if container.index_feed is None:
        raise HTTPException(503, "Источник E не подключён.")


router = APIRouter(
    prefix="/internal/v1/experience-index", dependencies=[Depends(require_index_reader)]
)


class IndexReference(BaseModel):
    """Ссылка на серверную редакцию; содержимое примера клиент не назначает."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    example_id: UUID
    example_revision: int = Field(ge=0, strict=True)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerifyRequest(BaseModel):
    """Ограниченный набор редакций для перепроверки после embedding либо поиска."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    references: list[IndexReference] = Field(max_length=100)


@router.get("/feed")
async def feed(
    container: Container,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    """Читает страницу проверенных примеров с продолжением по UUID."""
    return await container.index_feed.page(after=after, limit=limit)


@router.post("/verify")
async def verify(request: VerifyRequest, container: Container) -> dict:
    """Отбрасывает устаревшие либо исключённые примеры и возвращает доверенное содержимое."""
    return {
        "items": await container.index_feed.verify(
            tuple(item.model_dump(mode="json") for item in request.references)
        )
    }


@router.get("/crops/{example_id}/{index}")
async def crop(
    example_id: UUID,
    index: int,
    fingerprint: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")],
    container: Container,
) -> Response:
    """Отдаёт PNG только для совпавшей редакции пригодного примера."""
    try:
        content = await container.index_feed.crop(
            example_id=example_id, index=index, fingerprint=fingerprint
        )
    except ReviewConflictError as error:
        raise HTTPException(409, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except (ReviewError, OSError) as error:
        raise HTTPException(503, "Область Experience недоступна.") from error
    return Response(
        content, media_type="image/png", headers={"Cache-Control": "no-store"}
    )
