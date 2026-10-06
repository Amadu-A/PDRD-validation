# services/api-gateway/src/pdrd_api_gateway/infrastructure/experience_authors.py

"""Закрытый HTTP-клиент публичной проекции авторов из User Service."""

from dataclasses import dataclass, field
from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.core.observability import log_execution_time


@dataclass(frozen=True, slots=True)
class HttpExperienceAuthors:
    """Служебный ключ и подтверждённый actor никогда не приходят из браузерных полей."""

    base_url: str
    internal_key: str = field(repr=False)
    timeout_seconds: float = 10
    transport: httpx.AsyncBaseTransport | None = None

    @log_execution_time(operation="experience_author_profiles")
    async def read(
        self, *, actor_user_id: UUID, user_ids: tuple[UUID, ...]
    ) -> dict[UUID, dict]:
        """Ограничивает пакет ста UUID, проверяет ответ, запрещает перенаправления и прокси окружения."""
        result = {}
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            for offset in range(0, len(user_ids), 100):
                selected = user_ids[offset : offset + 100]
                try:
                    response = await client.post(
                        f"{self.base_url.rstrip('/')}/internal/v1/users/public-profiles",
                        headers={
                            "Authorization": f"Bearer {self.internal_key}",
                            "X-PDRD-Actor-Id": str(actor_user_id),
                        },
                        json={"user_ids": [str(value) for value in selected]},
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body.get("items"), list):
                        raise ValueError("Некорректные профили")
                    for row in body["items"]:
                        identity = UUID(row["user_id"])
                        if (
                            identity not in selected
                            or identity in result
                            or not (
                                (row["login"] is None or isinstance(row["login"], str))
                                and isinstance(row["display_name"], str)
                                and isinstance(row["roles"], list)
                                and all(isinstance(role, str) for role in row["roles"])
                            )
                        ):
                            raise ValueError("Несоответствующий профиль")
                        result[identity] = {
                            "login": row["login"],
                            "display_name": row["display_name"],
                            "roles": row["roles"],
                        }
                except (
                    httpx.HTTPError,
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                ) as error:
                    raise ReviewRequestError(
                        503, "Профили авторов временно недоступны."
                    ) from error
        return result
