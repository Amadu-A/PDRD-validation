#!/usr/bin/env python3
# scripts/check_auth_runtime.py

"""Проверяет реальный вход PDRD без сохранения или вывода пароля.

Скрипт запускается на сервере после ``scripts/up.sh``. По умолчанию он обращается
к loopback HTTP-порту frontend: это позволяет отдельно принять backend до
настройки браузерного HTTPS. Для удалённого адреса разрешён исключительно HTTPS.
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import ssl
import warnings
from dataclasses import dataclass
from http.cookies import SimpleCookie
from ipaddress import ip_address
from typing import Any
from urllib.parse import SplitResult, urlsplit


class AuthRuntimeCheckError(RuntimeError):
    """Runtime-контур вернул небезопасный или неожиданный результат."""


@dataclass(frozen=True, slots=True)
class CheckConfig:
    """Хранит публичный origin и локальную точку входа проверяемого стека."""

    transport_url: str
    public_origin: str
    login: str
    expect_admin: bool = False
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        """Запрещает передачу пароля по удалённому незашифрованному HTTP."""
        transport = _endpoint(self.transport_url, require_https=False)
        _endpoint(self.public_origin, require_https=True)
        if transport.scheme == "http" and not _loopback(transport.hostname or ""):
            raise ValueError("HTTP transport разрешён только через loopback сервера")
        if not self.login or self.login != self.login.strip():
            raise ValueError("Логин должен быть непустым и без внешних пробелов")
        if self.timeout_seconds <= 0:
            raise ValueError("Таймаут должен быть положительным")


@dataclass(frozen=True, slots=True)
class HttpResult:
    """Хранит ответ и Set-Cookie в памяти; эти значения не выводятся в лог."""

    status: int
    body: bytes
    set_cookie: str | None


def _endpoint(value: str, *, require_https: bool) -> SplitResult:
    """Разбирает origin без пути, query, fragment и userinfo."""
    parsed = urlsplit(value.rstrip("/"))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Требуется полный origin без пути, query и fragment")
    if require_https and parsed.scheme != "https":
        raise ValueError("Публичный origin PDRD должен использовать HTTPS")
    return parsed


def _loopback(hostname: str) -> bool:
    """Разрешает локальную диагностику только через loopback hostname/IP."""
    if hostname.casefold() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _request(
    config: CheckConfig,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    cookie: str | None = None,
    csrf_token: str | None = None,
) -> HttpResult:
    """Отправляет один запрос и не включает session/password в диагностику."""
    endpoint = _endpoint(config.transport_url, require_https=False)
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    if endpoint.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            endpoint.hostname,
            port,
            timeout=config.timeout_seconds,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(
            endpoint.hostname,
            port,
            timeout=config.timeout_seconds,
        )

    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method in {"POST", "PATCH", "DELETE"}:
        headers["Origin"] = config.public_origin.rstrip("/")
    if cookie:
        headers["Cookie"] = f"pdrd_session={cookie}"
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return HttpResult(
            status=response.status,
            body=response.read(),
            set_cookie=response.getheader("Set-Cookie"),
        )
    except OSError as error:
        raise AuthRuntimeCheckError(f"HTTP-контур PDRD недоступен: {error}") from None
    finally:
        connection.close()


def _json_object(
    result: HttpResult, expected_status: int, action: str
) -> dict[str, Any]:
    """Проверяет статус и JSON object, не печатая потенциальные токены."""
    if result.status != expected_status:
        raise AuthRuntimeCheckError(
            f"{action}: ожидался HTTP {expected_status}, получен {result.status}"
        )
    try:
        payload = json.loads(result.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AuthRuntimeCheckError(f"{action}: ответ не является JSON") from None
    if not isinstance(payload, dict):
        raise AuthRuntimeCheckError(f"{action}: ожидался JSON object")
    return payload


def _session_cookie(result: HttpResult) -> str:
    """Извлекает opaque cookie только в память и проверяет защитные атрибуты."""
    raw_cookie = result.set_cookie or ""
    parsed = SimpleCookie()
    parsed.load(raw_cookie)
    morsel = parsed.get("pdrd_session")
    if morsel is None or not morsel.value:
        raise AuthRuntimeCheckError("Login не выдал pdrd_session cookie")
    for attribute in ("secure", "httponly"):
        if morsel[attribute] is not True:
            raise AuthRuntimeCheckError(
                f"Login cookie не содержит обязательный атрибут {attribute}"
            )
    if morsel["samesite"].casefold() != "lax":
        raise AuthRuntimeCheckError("Login cookie должен содержать SameSite=Lax")
    return morsel.value


def run_check(config: CheckConfig, password: str) -> dict[str, Any]:
    """Проходит Login → Session → Profile → Admin policy → Logout."""
    if not password:
        raise ValueError("Пароль не должен быть пустым")

    login_result = _request(
        config,
        "POST",
        "/api/v1/auth/login",
        payload={"login": config.login, "password": password},
    )
    login_payload = _json_object(login_result, 200, "Login")
    if login_payload.get("authenticated") is not True:
        raise AuthRuntimeCheckError("Login не подтвердил аутентификацию")
    cookie = _session_cookie(login_result)

    session_result = _request(config, "GET", "/api/v1/auth/session", cookie=cookie)
    session = _json_object(session_result, 200, "Session")
    if session.get("authenticated") is not True:
        raise AuthRuntimeCheckError("Session не видит выданную cookie")
    user = session.get("user")
    if not isinstance(user, dict) or str(user.get("login", "")).casefold() != (
        config.login.casefold()
    ):
        raise AuthRuntimeCheckError("Session вернула профиль другого пользователя")
    csrf = session.get("csrf_token")
    if not isinstance(csrf, str) or len(csrf) < 32:
        raise AuthRuntimeCheckError("Session не выдала допустимый CSRF token")

    try:
        profile = _json_object(
            _request(config, "GET", "/api/v1/users/me", cookie=cookie),
            200,
            "Profile",
        )
        if profile.get("user_id") != user.get("user_id"):
            raise AuthRuntimeCheckError("Profile и Session ссылаются на разные user_id")

        for page in ("/account.html", "/admin.html"):
            page_result = _request(config, "GET", page, cookie=cookie)
            if (
                page_result.status != 200
                or b"<!doctype html>" not in page_result.body.lower()
            ):
                raise AuthRuntimeCheckError(f"Frontend page {page} недоступна")

        admin_result = _request(config, "GET", "/api/v1/admin/users", cookie=cookie)
        expected_admin_status = 200 if config.expect_admin else 403
        if admin_result.status != expected_admin_status:
            raise AuthRuntimeCheckError(
                "Admin policy: ожидался HTTP "
                f"{expected_admin_status}, получен {admin_result.status}"
            )

    finally:
        # Ошибка профиля, HTML или прав тоже должна закрыть тестовую сессию.
        _json_object(
            _request(
                config,
                "POST",
                "/api/v1/auth/logout",
                cookie=cookie,
                csrf_token=csrf,
            ),
            200,
            "Logout",
        )
    after_logout = _json_object(
        _request(config, "GET", "/api/v1/auth/session", cookie=cookie),
        200,
        "Session after logout",
    )
    if after_logout.get("authenticated") is not False:
        raise AuthRuntimeCheckError("Logout не завершил серверную сессию")
    return user


def _parser() -> argparse.ArgumentParser:
    """Описывает безопасный CLI без аргумента для пароля."""
    parser = argparse.ArgumentParser(
        description="Проверить реальный корпоративный вход и браузерную сессию PDRD"
    )
    parser.add_argument(
        "--transport-url",
        default="http://127.0.0.1:8080",
        help="локальный frontend origin для диагностики или реальный HTTPS origin",
    )
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--login", required=True)
    parser.add_argument("--expect-admin", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser


def main() -> int:
    """Запрашивает пароль через TTY и выводит только несекретный итог."""
    args = _parser().parse_args()
    try:
        config = CheckConfig(
            transport_url=args.transport_url,
            public_origin=args.public_origin,
            login=args.login,
            expect_admin=args.expect_admin,
            timeout_seconds=args.timeout_seconds,
        )
        # getpass без TTY может перейти к видимому чтению stdin: это запрещено.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Пароль AD: ")
        try:
            user = run_check(config, password)
        finally:
            password = ""
    except (getpass.GetPassWarning, EOFError):
        print("AUTH RUNTIME CHECK FAILED: нужен терминал со скрытым вводом пароля")
        return 1
    except (ValueError, AuthRuntimeCheckError) as error:
        print(f"AUTH RUNTIME CHECK FAILED: {error}")
        return 1

    roles = user.get("roles")
    role_text = (
        ", ".join(str(role) for role in roles) if isinstance(roles, list) else ""
    )
    print(f"Пользователь: {user.get('login')} ({user.get('user_id')})")
    print(f"Роли: {role_text or 'не назначены'}")
    print("AUTH RUNTIME CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
