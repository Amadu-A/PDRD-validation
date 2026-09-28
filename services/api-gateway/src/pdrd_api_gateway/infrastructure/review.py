# services/api-gateway/src/pdrd_api_gateway/infrastructure/review.py

"""Контекст оператора закрытого фронта и HTTP-адаптер Experience Service.

Служебные ключи передаются только между контейнерами. Они не являются
пользовательскими токенами и не выдаются браузеру.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.review import (
    ReviewContext,
    ReviewOperation,
    ReviewRequestError,
)


@dataclass(frozen=True, slots=True)
class ControlledReviewContext:
    """Фиксированный серверный оператор до подключения настоящей авторизации."""

    actor: str

    def resolve(self, *, job_id: UUID, operation: ReviewOperation) -> ReviewContext:
        """Игнорирует JSON и заголовки браузера, используя только конфигурацию сервера."""
        return ReviewContext(actor=self.actor, job_id=job_id, operation=operation)


@dataclass(frozen=True, slots=True)
class HttpReviewService:
    """Вызывает закрытые маршруты с ограничением времени и без перенаправлений."""

    base_url: str
    internal_key: str = field(repr=False)
    timeout_seconds: float = 300
    transport: httpx.AsyncBaseTransport | None = None

    async def execute(
        self, *, context: ReviewContext, command: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Не пересылает произвольные пользовательские заголовки в Experience."""
        suffix = {"open": "/open", "read": "", "command": "/commands"}[
            context.operation
        ]
        method = "GET" if context.operation == "read" else "POST"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url.rstrip('/')}/internal/v1/reviews/{context.job_id}{suffix}",
                    headers={
                        "Authorization": f"Bearer {self.internal_key}",
                        "X-Review-Actor": context.actor,
                    },
                    json=command if context.operation == "command" else None,
                )
        except httpx.HTTPError as error:
            raise ReviewRequestError(
                503, "Хранилище Review временно недоступно."
            ) from error
        if response.status_code not in {200, 404, 409, 422}:
            raise ReviewRequestError(503, "Хранилище Review временно недоступно.")
        try:
            payload = response.json()
        except ValueError as error:
            raise ReviewRequestError(
                503, "Хранилище вернуло некорректный ответ."
            ) from error
        if not isinstance(payload, dict):
            raise ReviewRequestError(503, "Хранилище вернуло некорректный ответ.")
        if response.status_code in {404, 409, 422}:
            detail = payload.get("detail")
            raise ReviewRequestError(
                response.status_code,
                detail if isinstance(detail, str) else "Некорректная команда Review.",
            )
        if not isinstance(payload, dict) or payload.get("job_id") != str(
            context.job_id
        ):
            raise ReviewRequestError(
                503, "Хранилище вернуло некорректный снимок Review."
            )
        return payload
