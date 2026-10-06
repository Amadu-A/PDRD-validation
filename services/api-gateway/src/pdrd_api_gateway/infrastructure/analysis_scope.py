# services/api-gateway/src/pdrd_api_gateway/infrastructure/analysis_scope.py

"""Закрытая проверка областей Review через актуальные данные user-service.

Gateway передаёт только два внутренних UUID. Решение о действующем назначении
руководителя и совпадении отдела принимает user-service в своей транзакции.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from pdrd_api_gateway.application.ports.analysis_scope import AnalysisScopeUnavailable


@dataclass(frozen=True, slots=True)
class HttpUserAnalysisScopeChecker:
    """Запрашивает только доверенный internal API и закрывает ошибки сервиса."""

    base_url: str
    internal_key: str = field(repr=False)
    timeout_seconds: float = 10.0
    client_factory: Callable[[], httpx.AsyncClient] | None = field(
        default=None, repr=False
    )

    def _client(self) -> httpx.AsyncClient:
        """Отключает redirects и системные прокси внутренних запросов."""
        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(
            timeout=self.timeout_seconds, trust_env=False, follow_redirects=False
        )

    async def allows(self, *, actor_user_id: UUID, owner_user_id: UUID) -> bool:
        """Возвращает только подтверждённый user-service boolean."""
        if not self.internal_key:
            raise AnalysisScopeUnavailable("Не настроен сервисный ключ")
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/internal/v1/access/review-scope",
                    headers={"Authorization": f"Bearer {self.internal_key}"},
                    json={
                        "actor_user_id": str(actor_user_id),
                        "owner_user_id": str(owner_user_id),
                    },
                )
        except httpx.HTTPError as error:
            raise AnalysisScopeUnavailable(
                "Каталог пользователей недоступен"
            ) from error

        if response.status_code != 200:
            raise AnalysisScopeUnavailable("Каталог пользователей не подтвердил доступ")
        try:
            body = response.json()
        except ValueError as error:
            raise AnalysisScopeUnavailable(
                "Каталог вернул некорректный ответ"
            ) from error
        if not isinstance(body, dict) or type(body.get("allowed")) is not bool:
            raise AnalysisScopeUnavailable("Каталог вернул некорректное решение")
        return body["allowed"]
