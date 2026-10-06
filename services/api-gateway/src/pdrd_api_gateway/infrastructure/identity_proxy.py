# services/api-gateway/src/pdrd_api_gateway/infrastructure/identity_proxy.py

"""Одноимённая пересылка запросов Auth/Admin без передачи служебных заголовков.

На каждый запрос создаётся отдельный HTTP-клиент: его cookie jar не может
сохранить cookie одного посетителя и отправить её следующему.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import httpx

from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings

ServiceName = Literal["auth", "admin"]
ALLOWED_REQUEST_HEADERS = (
    "accept",
    "content-type",
    "cookie",
    "origin",
    "x-csrf-token",
)
SAFE_SUFFIX = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")


class ProxyUnavailable(Exception):
    """Закрытый сервис не ответил, его внутренний адрес не раскрывается клиенту."""


@dataclass(frozen=True, slots=True)
class ProxyReply:
    """Ограниченный ответ с отдельными заголовками Set-Cookie."""

    status_code: int
    content: bytes
    content_type: str | None
    set_cookies: tuple[str, ...]
    retry_after: str | None


class IdentityProxy:
    """Пересылает только разрешённые браузерные заголовки по заданному пути."""

    def __init__(
        self,
        settings: IdentityProxySettings,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        """Принимает проверенные адреса и фабрику изолированного HTTP-клиента."""
        self._settings = settings
        self._client_factory = client_factory or self._build_client

    def _build_client(self) -> httpx.AsyncClient:
        """Отключает redirects и системные прокси для внутренних адресов."""
        return httpx.AsyncClient(
            timeout=self._settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def forward(
        self,
        *,
        service: ServiceName,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        body: bytes,
        client_ip: str,
    ) -> ProxyReply:
        """Не пропускает клиентский Actor ID, Bearer и X-Forwarded заголовки."""
        if service == "auth":
            base_url = self._settings.auth_base_url
            allowed_path = path == "/api/v1/users/me" or self._has_safe_suffix(
                path, "/api/v1/auth/"
            )
        elif service == "admin":
            base_url = self._settings.admin_base_url
            allowed_path = self._has_safe_suffix(path, "/api/v1/admin/")
        else:
            raise ValueError("Неизвестный сервис для проксирования")
        if not allowed_path or method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Недопустимый маршрут внутреннего прокси")
        destination = f"{base_url}{path}"
        if query:
            destination = f"{destination}?{query}"
        outgoing = {
            name: value
            for name in ALLOWED_REQUEST_HEADERS
            if (value := headers.get(name)) is not None
        }
        outgoing["x-pdrd-client-ip"] = client_ip
        try:
            async with self._client_factory() as client:
                upstream = await client.request(
                    method, destination, headers=outgoing, content=body
                )
        except httpx.HTTPError as error:
            raise ProxyUnavailable("Закрытый сервис временно недоступен") from error
        return ProxyReply(
            status_code=upstream.status_code,
            content=upstream.content,
            content_type=upstream.headers.get("content-type"),
            set_cookies=tuple(upstream.headers.get_list("set-cookie")),
            retry_after=upstream.headers.get("retry-after"),
        )

    @staticmethod
    def _has_safe_suffix(path: str, prefix: str) -> bool:
        """Запрещает пустой путь, точечный обход и неожиданные разделители."""
        return (
            path.startswith(prefix)
            and SAFE_SUFFIX.fullmatch(path[len(prefix) :]) is not None
        )
