# services/experience-service/src/pdrd_experience_service/transport/http/routers/review.py

"""Закрытые маршруты Review для API Gateway с проверкой служебного ключа.

Ключ и X-Review-Actor допустимы только на внутреннем соединении Gateway.
Публичный Gateway получает инженера из серверного адаптера, не из браузера.
"""

import secrets
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from pdrd_experience_service.application.ports.analysis_source import (
    AnalysisSourceUnavailableError,
)
from pdrd_experience_service.core.container import ApplicationContainer
from pdrd_experience_service.domain.review import (
    ReviewConflictError,
    ReviewError,
    ReviewNotReadyError,
    ReviewSession,
)
from pdrd_experience_service.transport.http.dependencies import get_container
from pdrd_experience_service.transport.http.review_commands import execute_command
from pdrd_experience_service.transport.http.schemas.review import ReviewCommand

router = APIRouter(prefix="/internal/v1/reviews", tags=["review-internal"])


def trusted_actor(
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> str:
    """Проверяет ключ до обработки операции и принимает контекст только от Gateway."""
    token = container.settings.internal_key.get_secret_value()
    supplied = request.headers.get("authorization", "")
    if not token or not secrets.compare_digest(
        supplied.encode("utf-8"), f"Bearer {token}".encode()
    ):
        raise HTTPException(403, "Доступ разрешён только API Gateway.")
    actor = request.headers.get("x-review-actor", "").strip()
    if not 1 <= len(actor) <= 128:
        raise HTTPException(403, "Отсутствует доверенный контекст инженера.")
    return actor


def snapshot(session: ReviewSession) -> dict:
    """Возвращает текущее состояние; полный аудит остаётся в серверном хранилище."""
    result = jsonable_encoder(asdict(session), exclude={"history"})
    result["pending_count"] = session.pending_count
    for row, finding in zip(result["findings"], session.findings, strict=True):
        row["experience_tag"] = finding.experience_tag
    return result


async def current_snapshot(
    container: ApplicationContainer, session: ReviewSession
) -> dict:
    """Добавляет версии подтверждений, не выдавая VLM-предложения за проверку."""
    result = snapshot(session)
    result["area_confirmations"] = (
        jsonable_encoder(
            [
                asdict(area)
                for area in await container.confirmed_areas.load_status(review=session)
            ]
        )
        if container.confirmed_areas is not None
        else []
    )
    return result


@router.post("/{job_id}/open")
async def open_review(
    job_id: UUID,
    actor: Annotated[str, Depends(trusted_actor)],
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Открывает или восстанавливает Review только из серверного анализа."""
    if container.open_review is None:
        raise HTTPException(503, "Источник анализа не подключён.")
    try:
        return await current_snapshot(
            container, await container.open_review.execute(job_id=job_id, actor=actor)
        )
    except LookupError as error:
        raise HTTPException(404, "Завершённый анализ не найден.") from error
    except AnalysisSourceUnavailableError as error:
        raise HTTPException(
            503, "Серверный источник анализа временно недоступен."
        ) from error
    except ReviewError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/{job_id}")
async def get_review(
    job_id: UUID,
    actor: Annotated[str, Depends(trusted_actor)],
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Читает сохранённую редакцию без неявного создания Review."""
    del actor
    session = await container.reviews.load(job_id)
    if session is None:
        raise HTTPException(404, "Human Review ещё не открыт.")
    return await current_snapshot(container, session)


@router.post("/{job_id}/commands")
async def change_review(
    job_id: UUID,
    command: ReviewCommand,
    actor: Annotated[str, Depends(trusted_actor)],
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Применяет одну команду с CAS, оригиналами и журналом в одной транзакции."""
    try:
        return await current_snapshot(
            container,
            await execute_command(
                container.change_review,
                job_id=job_id,
                actor=actor,
                command=command,
                confirm_area=container.confirm_area,
                revoke_area=container.revoke_area,
            ),
        )
    except ReviewConflictError as error:
        raise HTTPException(
            409, "Review изменён в другой вкладке. Загрузите актуальную редакцию."
        ) from error
    except LookupError as error:
        raise HTTPException(404, "Human Review ещё не открыт.") from error
    except ReviewError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/{job_id}/pdf-manifest")
async def reviewed_manifest(
    job_id: UUID,
    actor: Annotated[str, Depends(trusted_actor)],
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Передаёт Gateway только утверждённую проекцию и проверенные координаты."""
    del actor
    if container.export_review is None:
        raise HTTPException(503, "Экспорт Review не подключён.")
    try:
        return await container.export_review.execute(job_id=job_id)
    except LookupError as error:
        raise HTTPException(404, "Human Review ещё не открыт.") from error
    except (ReviewConflictError, ReviewNotReadyError) as error:
        raise HTTPException(
            409, "Рассмотрите все замечания и утвердите актуальную редакцию Review."
        ) from error
    except ReviewError as error:
        raise HTTPException(422, "Некорректные данные Human Review.") from error
