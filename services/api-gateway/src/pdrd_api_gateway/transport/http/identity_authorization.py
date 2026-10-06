# services/api-gateway/src/pdrd_api_gateway/transport/http/identity_authorization.py

"""Проверяет серверные права для старых маршрутов через действующую сессию Auth Service.

Проверка включается вместе с auth-профилем; отсутствие ответа Auth Service закрывает
защищённые операции. Гостевой запуск анализа и ТЗ остаётся открытым.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings


class SectionAccessUnavailable(RuntimeError):
    """User Service не подтвердил актуальный набор разделов."""


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Доверенный ответ Auth Service для одного HTTP-запроса Gateway."""

    user_id: UUID
    permissions: frozenset[str]


def required_permission(
    method: str, path: str, payload: dict[str, object] | None = None
) -> tuple[str, ...] | None:
    """Возвращает допустимые полномочия для защищённого действия."""
    payload = payload or {}
    segments = path.strip("/").split("/")
    if len(segments) < 3 or segments[:2] != ["api", "v1"]:
        return None

    if segments[2] == "normative":
        if "technical-assignments" in segments:
            return None
        if "user-packages" in segments:
            return (
                ("user_documents.own.read", "user_documents.scoped.read")
                if method == "GET"
                else ("user_documents.own.write", "user_documents.scoped.write")
            )
        if method == "GET":
            return None
        if method == "DELETE":
            return ("normative.delete",)
        if method == "PATCH" and "system_prompt" in payload:
            return ("system_prompt.manage",)
        return ("normative.write",)

    if segments[2] == "review" and segments[3:] == ["config"]:
        return ("review.own.read", "review.scoped.read")

    if segments[2] == "analyses" and len(segments) >= 5:
        if segments[4] == "reviewed-pdf":
            return ("review.pdf.download",)
        if segments[4] == "review":
            if len(segments) == 6 and segments[5] == "commands":
                action = payload.get("action")
                if action == "add":
                    return ("review.gold.create",)
                if action == "approve":
                    return ("review.approve",)
                if action in {"edit", "geometry"} and str(
                    payload.get("finding_id", "")
                ).startswith("manual:"):
                    return ("review.gold.create",)
                return ("review.findings.decide",)
            return ("review.own.read", "review.scoped.read")

    if segments[2] == "experience":
        if method == "POST" and len(segments) == 5 and segments[3] == "capture":
            return ("experience.capture",)
        # Общий каталог пока не фильтрует записи по владельцу/отделу.
        return ("admin.access",)

    if segments[2] == "experience-versions":
        # Версии содержат задания разных сотрудников; до scope filtering
        # этот API доступен только администратору платформы.
        return ("admin.access",)
    return None


def needs_json_body(method: str, path: str) -> bool:
    """Читает тело только двух небольших JSON-команд, где право зависит от действия."""
    return (method == "PATCH" and path.startswith("/api/v1/normative/sections/")) or (
        method == "POST" and path.endswith("/review/commands")
    )


class IdentityAuthorizer:
    """Получает актуальные права из Auth Service, игнорируя клиентские claims и заголовки."""

    def __init__(
        self,
        settings: IdentityProxySettings,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        """Хранит только сервисный ключ и проверенные адреса конфигурации."""
        self._settings = settings
        self._client_factory = client_factory or self._new_client

    def _new_client(self) -> httpx.AsyncClient:
        """Не использует системный HTTP-прокси для внутреннего запроса."""
        return httpx.AsyncClient(
            timeout=self._settings.timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )

    def origin_denial(self, request: Request) -> JSONResponse | None:
        """Закрывает изменяющие запросы с чужого сайта и без Origin."""
        if (
            request.method not in {"GET", "HEAD"}
            and request.headers.get("origin") != self._settings.public_origin
        ):
            return JSONResponse(
                status_code=403, content={"detail": "Недопустимый источник запроса"}
            )
        return None

    async def authenticate_if_present(self, request: Request) -> JSONResponse | None:
        """Проверяет предъявленную cookie; неверная не превращается в гостевой запрос."""
        result = await self._identity(request)
        return result if isinstance(result, JSONResponse) else None

    async def _identity(
        self, request: Request
    ) -> VerifiedIdentity | JSONResponse | None:
        """Получает и кеширует только на время запроса текущие серверные права."""
        cached = getattr(request.state, "verified_identity", None)
        if isinstance(cached, VerifiedIdentity):
            return cached
        token = request.cookies.get("pdrd_session")
        if not token:
            request.state.identity_user_id = None
            return None
        try:
            async with self._client_factory() as client:
                result = await client.post(
                    f"{self._settings.auth_base_url}/internal/v1/auth/introspect",
                    headers={
                        "Authorization": (
                            "Bearer "
                            + self._settings.auth_internal_key.get_secret_value()
                        )
                    },
                    json={"token": token},
                )
        except httpx.HTTPError:
            return JSONResponse(
                status_code=503, content={"detail": "Проверка прав недоступна"}
            )
        if result.status_code == 401:
            return JSONResponse(status_code=401, content={"detail": "Требуется вход"})
        if result.status_code != 200:
            return JSONResponse(
                status_code=503, content={"detail": "Проверка прав недоступна"}
            )
        try:
            body = result.json()
            granted = body["permissions"]
            if not isinstance(granted, list) or not all(
                isinstance(value, str) for value in granted
            ):
                raise ValueError
            user_id = UUID(str(body["user_id"]))
        except (ValueError, KeyError, TypeError):
            return JSONResponse(
                status_code=503, content={"detail": "Проверка прав недоступна"}
            )
        identity = VerifiedIdentity(user_id, frozenset(granted))
        request.state.verified_identity = identity
        request.state.identity_user_id = user_id
        return identity

    async def allowed_sections(self, request: Request) -> frozenset[UUID] | None:
        """Читает UUID назначений у User Service; None означает платформенный доступ."""
        identity = getattr(request.state, "verified_identity", None)
        if not isinstance(identity, VerifiedIdentity):
            return None
        if "admin.access" in identity.permissions:
            return None
        cached = getattr(request.state, "allowed_section_ids", None)
        if cached is not None:
            return cached
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    f"{self._settings.user_service_url}/internal/v1/users/{identity.user_id}/section-access",
                    headers={
                        "Authorization": "Bearer "
                        + self._settings.user_service_internal_key.get_secret_value(),
                        "X-PDRD-Actor-Id": str(identity.user_id),
                    },
                )
            response.raise_for_status()
            payload = response.json()
            if (
                UUID(payload["user_id"]) != identity.user_id
                or payload["all_sections"] is not False
            ):
                raise ValueError("Некорректная область")
            if not isinstance(payload["section_ids"], list):
                raise ValueError("Некорректный список")
            result = frozenset(UUID(value) for value in payload["section_ids"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise SectionAccessUnavailable(
                "Не удалось проверить доступ к разделам"
            ) from error
        request.state.allowed_section_ids = result
        return result

    async def require_section(
        self, request: Request, section_id: UUID
    ) -> JSONResponse | None:
        """Не допускает обращение вошедшего пользователя к неназначенному разделу."""
        try:
            allowed = await self.allowed_sections(request)
        except SectionAccessUnavailable:
            return JSONResponse(
                status_code=503,
                content={"detail": "Проверка доступа к разделам недоступна"},
            )
        if allowed is not None and section_id not in allowed:
            return JSONResponse(
                status_code=403, content={"detail": "Нет доступа к выбранному разделу"}
            )
        return None

    async def require(
        self, request: Request, permissions: tuple[str, ...]
    ) -> JSONResponse | None:
        """Разрешает запрос только при активной cookie и хотя бы одном требуемом праве."""
        if denial := self.origin_denial(request):
            return denial
        identity = await self._identity(request)
        if isinstance(identity, JSONResponse):
            return identity
        if identity is None:
            return JSONResponse(status_code=401, content={"detail": "Требуется вход"})
        if not any(permission in identity.permissions for permission in permissions):
            return JSONResponse(
                status_code=403, content={"detail": "Недостаточно прав"}
            )
        return None


async def enforce_identity_authorization(
    request: Request,
    authorizer: IdentityAuthorizer,
) -> JSONResponse | None:
    """Проверяет защищённый маршрут до передачи тела и операций нижним сервисам."""
    if (
        request.method == "POST"
        and request.url.path
        in {
            "/api/v1/analyses",
            "/api/v1/analyses/project-context/preflight",
            "/api/v1/normative/technical-assignments/prepare",
        }
        and (denial := authorizer.origin_denial(request)) is not None
    ):
        return denial
    payload: dict[str, object] | None = None
    if needs_json_body(request.method, request.url.path):
        raw = await request.body()
        if len(raw) > 64 * 1024:
            return JSONResponse(
                status_code=413, content={"detail": "Слишком большая команда"}
            )
        try:
            parsed = json.loads(raw)
            payload = parsed if isinstance(parsed, dict) else {}
        except (ValueError, UnicodeError):
            payload = {}
    required = required_permission(request.method, request.url.path, payload)
    if required is None:
        return None
    return await authorizer.require(request, required)
