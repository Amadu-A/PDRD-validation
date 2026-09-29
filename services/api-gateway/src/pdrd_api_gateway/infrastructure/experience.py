# services/api-gateway/src/pdrd_api_gateway/infrastructure/experience.py

"""Закрытый оператор и HTTP-мост Experience; служебные ключи не выдаются браузеру."""

from dataclasses import dataclass, field
from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.experience import (
    ExperienceContext,
    ExperienceOperation,
)
from pdrd_api_gateway.application.ports.review import (
    ReviewAccessPolicy,
    ReviewContext,
    ReviewRequestError,
)


@dataclass(frozen=True, slots=True)
class ControlledExperienceContext:
    """Серверный инженер до появления полноценной авторизации."""

    actor: str

    def resolve(
        self,
        *,
        operation: ExperienceOperation,
        job_id: UUID | None,
        example_id: UUID | None,
    ) -> ExperienceContext:
        """Не читает actor или права из запроса браузера."""
        return ExperienceContext(self.actor, operation, job_id, example_id)


@dataclass(frozen=True, slots=True)
class ControlledExperienceAccess:
    """В закрытом режиме каталог доступен оператору; задание проверяется отдельно."""

    operator: str
    reviews: ReviewAccessPolicy

    async def require(self, context: ExperienceContext) -> None:
        """Новая политика прав сможет заменить адаптер без изменения бизнес-API."""
        if not context.actor or context.actor != self.operator:
            raise ReviewRequestError(403, "Нет доверенного контекста каталога.")
        if context.job_id is not None:
            await self.reviews.require(
                ReviewContext(context.actor, context.job_id, "read")
            )


@dataclass(frozen=True, slots=True)
class HttpExperienceService:
    """Фиксированные маршруты, timeout и запрет redirects."""

    base_url: str
    internal_key: str = field(repr=False)
    transport: httpx.AsyncBaseTransport | None = None

    async def execute(
        self,
        *,
        context: ExperienceContext,
        query: dict | None,
        command: dict | None,
        index: int | None,
    ) -> dict | bytes:
        """Actor приходит из сервера Gateway; browser headers не пересылаются."""
        base = f"{self.base_url.rstrip('/')}/internal/v1/experience"
        example = str(context.example_id)
        operation = context.operation
        method, suffix = {
            "list": ("GET", ""),
            "export": ("GET", "/export"),
            "capture": ("POST", f"/capture/{context.job_id}"),
            "read": ("GET", f"/{example}"),
            "curate": ("PATCH", f"/{example}"),
            "deactivate": ("DELETE", f"/{example}"),
            "crop": ("GET", f"/{example}/crops/{index}"),
            "history": ("GET", f"/{example}/history"),
            "delete_selection": ("POST", "/delete-selection"),
            "selection_read": ("POST", "/read-selection"),
            "version_list": ("GET", ""),
            "version_create": ("POST", ""),
            "version_read": ("GET", f"/{example}"),
            "version_rename": ("PATCH", f"/{example}"),
            "version_delete": ("POST", f"/{example}/delete"),
            "version_apply": ("POST", f"/{example}/apply"),
        }[operation]
        if operation.startswith("version_"):
            base = f"{self.base_url.rstrip('/')}/internal/v1/experience-versions"
        try:
            async with httpx.AsyncClient(
                timeout=300, follow_redirects=False, transport=self.transport
            ) as client:
                response = await client.request(
                    method,
                    base + suffix,
                    headers={
                        "Authorization": f"Bearer {self.internal_key}",
                        "X-Review-Actor": context.actor,
                    },
                    params=query,
                    json=command if method in {"POST", "PATCH"} else None,
                )
        except httpx.HTTPError as error:
            raise ReviewRequestError(
                503, "Каталог Experience временно недоступен."
            ) from error
        if response.status_code in {404, 409, 422}:
            detail = None
            if operation.startswith("version_"):
                try:
                    payload = response.json()
                    detail = (
                        payload.get("detail") if isinstance(payload, dict) else None
                    )
                except ValueError:
                    pass
            raise ReviewRequestError(
                response.status_code,
                detail
                if isinstance(detail, str)
                else {
                    404: "Пример или Review не найден.",
                    409: "Редакция изменена или Review не утверждён.",
                    422: "Проверьте поля каталога и причину отрицательной разметки.",
                }[response.status_code],
            )
        if response.status_code != 200:
            raise ReviewRequestError(503, "Каталог Experience временно недоступен.")
        if operation in {"crop", "export"}:
            media = "image/png" if operation == "crop" else "application/zip"
            signature = b"\x89PNG\r\n\x1a\n" if operation == "crop" else b"PK"
            if response.headers.get("content-type", "").split(";")[
                0
            ] != media or not response.content.startswith(signature):
                raise ReviewRequestError(503, "Каталог вернул некорректный файл.")
            return response.content
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Некорректный JSON.")
            if (
                operation in {"read", "curate", "deactivate"}
                and payload.get("id") != example
            ):
                raise ValueError("Некорректный пример.")
            if operation == "capture" and payload.get("job_id") != str(context.job_id):
                raise ValueError("Некорректное задание.")
            if (
                operation in {"version_read", "version_rename", "version_delete"}
                and payload.get("id") != example
            ):
                raise ValueError("Некорректная версия.")
            return payload
        except (ValueError, TypeError) as error:
            raise ReviewRequestError(
                503, "Каталог вернул некорректный ответ."
            ) from error
