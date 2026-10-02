# services/api-gateway/src/pdrd_api_gateway/transport/http/analysis_access.py

"""Серверная проверка владельца задания до выдачи файлов и Review.

Middleware вызывает её только в режиме авторизации. UUID задания сам по себе
не является правом: гостю нужен отдельный случайный ключ с ограниченным сроком.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from pdrd_api_gateway.application.ports.analysis_scope import (
    AnalysisScopeChecker,
    AnalysisScopeUnavailable,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import GetAnalysisJob
from pdrd_api_gateway.domain.analysis_access import can_access_analysis_job


def analysis_job_route(path: str) -> tuple[UUID, bool] | None:
    """Выделяет job ID и помечает маршруты, которые материализуют Review."""
    segments = path.strip("/").split("/")
    if segments[:3] == ["api", "v1", "analyses"] and len(segments) >= 4:
        try:
            job_id = UUID(segments[3])
        except ValueError:
            return None
        review = len(segments) >= 5 and segments[4] in {"review", "reviewed-pdf"}
        return job_id, review

    if segments[:4] == ["api", "v1", "experience", "capture"] and len(segments) == 5:
        try:
            return UUID(segments[4]), True
        except ValueError:
            return None
    return None


async def enforce_analysis_job_access(
    request: Request,
    get_analysis_job: GetAnalysisJob | None,
    actor_user_id: UUID | None,
    *,
    scope_checker: AnalysisScopeChecker | None = None,
) -> JSONResponse | None:
    """Закрывает любой job URL без владельца или действующего guest capability."""
    route = analysis_job_route(request.url.path)
    if route is None:
        return None
    if request.method not in {"GET", "HEAD"}:
        public_origin = (
            request.app.state.container.settings.identity_proxy.public_origin
        )
        if request.headers.get("origin") != public_origin:
            return JSONResponse(
                status_code=403,
                content={"detail": "Недопустимый источник запроса"},
                headers={"Cache-Control": "no-store"},
            )
    if get_analysis_job is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Хранилище заданий недоступно"},
            headers={"Cache-Control": "no-store"},
        )
    job_id, review = route
    try:
        job = await get_analysis_job.execute(job_id=job_id)
    except (SQLAlchemyError, OSError, TimeoutError):
        return JSONResponse(
            status_code=503,
            content={"detail": "Проверка доступа недоступна"},
            headers={"Cache-Control": "no-store"},
        )
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Задание не найдено"},
            headers={"Cache-Control": "no-store"},
        )
    token = request.headers.get("x-pdrd-analysis-access") or request.query_params.get(
        "access_token"
    )
    scoped_authorized = False
    if (
        scope_checker is not None
        and actor_user_id is not None
        and job.owner_user_id is not None
        and actor_user_id != job.owner_user_id
    ):
        try:
            scoped_authorized = await scope_checker.allows(
                actor_user_id=actor_user_id, owner_user_id=job.owner_user_id
            )
        except AnalysisScopeUnavailable:
            return JSONResponse(
                status_code=503,
                content={"detail": "Проверка области доступа недоступна"},
                headers={"Cache-Control": "no-store"},
            )
    if can_access_analysis_job(
        job,
        actor_user_id=actor_user_id,
        guest_access_token=token,
        now=datetime.now(UTC),
        review=review,
        scoped_authorized=scoped_authorized,
    ):
        return None
    return JSONResponse(
        status_code=404,
        content={"detail": "Задание не найдено"},
        headers={"Cache-Control": "no-store"},
    )
