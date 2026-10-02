# tests/functional/test_auth_runtime_check.py

"""Проверяет безопасный сценарий runtime-приёмки Auth через локальный HTTP stub."""

from __future__ import annotations

import json
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import ClassVar

import pytest

from scripts import check_auth_runtime
from scripts.check_auth_runtime import AuthRuntimeCheckError, CheckConfig, run_check


class _AuthHandler(BaseHTTPRequestHandler):
    """Имитирует Gateway/Auth и фиксирует обязательные Origin/CSRF/cookie."""

    expect_admin: ClassVar[bool] = False
    logged_out: ClassVar[bool] = False
    profile_status: ClassVar[int] = 200
    cookie_header: ClassVar[str] = (
        "pdrd_session=opaque-session-token; Path=/; Secure; HttpOnly; SameSite=lax"
    )
    cookie = "opaque-session-token"
    csrf = "c" * 64
    user: ClassVar[dict[str, object]] = {
        "user_id": "7d65122c-52f8-4c76-bcab-7e02d78ecf43",
        "kind": "corporate",
        "tier": "member",
        "status": "active",
        "display_name": "Иван Мейн",
        "login": "i.mein",
        "email": None,
        "roles": [],
        "permissions": ["analysis.run", "profile.read"],
    }

    def log_message(self, format: str, *args: object) -> None:
        """Не пишет тестовые cookie и запросы в stderr."""

    def _json(self, status: int, payload: dict[str, object]) -> None:
        """Возвращает компактный JSON-ответ."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _has_cookie(self) -> bool:
        """Проверяет opaque cookie без публикации значения."""
        return self.headers.get("Cookie") == f"pdrd_session={self.cookie}"

    def do_POST(self) -> None:
        """Обрабатывает login/logout с Origin и CSRF."""
        assert self.headers.get("Origin") == "https://pdrd.itcneoterm.local"
        if self.path == "/api/v1/auth/login":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            assert payload == {"login": "i.mein", "password": "secret"}
            type(self).logged_out = False
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", self.cookie_header)
            body = b'{"authenticated":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/v1/auth/logout":
            assert self._has_cookie()
            assert self.headers.get("X-CSRF-Token") == self.csrf
            type(self).logged_out = True
            self._json(200, {"message": "ok"})
            return
        self._json(404, {"detail": "not found"})

    def do_GET(self) -> None:
        """Возвращает session/profile/pages и ролевой ответ Admin API."""
        if self.path == "/api/v1/auth/session":
            authenticated = self._has_cookie() and not type(self).logged_out
            self._json(
                200,
                {
                    "authenticated": authenticated,
                    "user": self.user if authenticated else None,
                    "session": {"session_id": "session"} if authenticated else None,
                    **({"csrf_token": self.csrf} if authenticated else {}),
                },
            )
            return
        if self.path == "/api/v1/users/me" and self._has_cookie():
            self._json(self.profile_status, self.user)
            return
        if self.path in {"/account.html", "/admin.html"}:
            body = b"<!doctype html><title>PDRD</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/v1/admin/users" and self._has_cookie():
            self._json(200 if self.expect_admin else 403, {"items": []})
            return
        self._json(404, {"detail": "not found"})


@pytest.fixture
def auth_server() -> tuple[str, type[_AuthHandler]]:
    """Запускает локальный HTTP stub и гарантированно завершает его."""
    _AuthHandler.expect_admin = False
    _AuthHandler.logged_out = False
    _AuthHandler.profile_status = 200
    _AuthHandler.cookie_header = (
        "pdrd_session=opaque-session-token; Path=/; Secure; HttpOnly; SameSite=lax"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _AuthHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_runtime_check_covers_login_profile_policy_and_logout(
    auth_server: tuple[str, type[_AuthHandler]],
) -> None:
    """Обычный сотрудник проходит вход, но не получает Admin API."""
    transport, _ = auth_server
    user = run_check(
        CheckConfig(
            transport_url=transport,
            public_origin="https://pdrd.itcneoterm.local",
            login="i.mein",
        ),
        "secret",
    )

    assert user["login"] == "i.mein"
    assert _AuthHandler.logged_out is True


def test_runtime_check_accepts_bootstrapped_admin(
    auth_server: tuple[str, type[_AuthHandler]],
) -> None:
    """После bootstrap ожидается доступ Admin API с той же сессией."""
    transport, handler = auth_server
    handler.expect_admin = True

    run_check(
        CheckConfig(
            transport_url=transport,
            public_origin="https://pdrd.itcneoterm.local",
            login="i.mein",
            expect_admin=True,
        ),
        "secret",
    )


@pytest.mark.parametrize(
    ("transport", "origin"),
    [
        ("http://192.168.55.3:8080", "https://pdrd.itcneoterm.local"),
        ("http://127.0.0.1:8080", "http://192.168.55.3:8080"),
    ],
)
def test_runtime_check_rejects_insecure_boundaries(transport: str, origin: str) -> None:
    """Пароль нельзя отправить на удалённый HTTP или принять HTTP public origin."""
    with pytest.raises(ValueError):
        CheckConfig(transport_url=transport, public_origin=origin, login="i.mein")


@pytest.mark.parametrize("failure", ["profile", "admin-policy"])
def test_runtime_check_logs_out_when_acceptance_fails(
    auth_server: tuple[str, type[_AuthHandler]], failure: str
) -> None:
    """Ошибка приёмки не оставляет действующую тестовую сессию."""
    transport, handler = auth_server
    if failure == "profile":
        handler.profile_status = 503
    else:
        handler.expect_admin = True

    with pytest.raises(AuthRuntimeCheckError, match=r"Profile|Admin policy"):
        run_check(
            CheckConfig(
                transport_url=transport,
                public_origin="https://pdrd.itcneoterm.local",
                login="i.mein",
            ),
            "secret",
        )

    assert handler.logged_out is True


def test_runtime_check_rejects_attribute_names_inside_cookie_value(
    auth_server: tuple[str, type[_AuthHandler]],
) -> None:
    """Текст Secure/HttpOnly в значении токена не заменяет атрибуты cookie."""
    transport, handler = auth_server
    handler.cookie_header = 'pdrd_session="secure-httponly-samesite=lax"; Path=/'

    with pytest.raises(AuthRuntimeCheckError, match="атрибут secure"):
        run_check(
            CheckConfig(
                transport_url=transport,
                public_origin="https://pdrd.itcneoterm.local",
                login="i.mein",
            ),
            "secret",
        )


def test_runtime_cli_refuses_visible_password_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """При отсутствии скрытого ввода getpass не переходит к чтению пароля."""

    def no_terminal(prompt: str) -> str:
        """Имитирует предупреждение getpass перед небезопасным fallback."""
        warnings.warn(
            "visible fallback", check_auth_runtime.getpass.GetPassWarning, stacklevel=2
        )
        raise AssertionError("Видимый ввод не должен выполняться")

    monkeypatch.setattr(check_auth_runtime.getpass, "getpass", no_terminal)
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_auth_runtime.py",
            "--public-origin",
            "https://pdrd.itcneoterm.local",
            "--login",
            "i.mein",
        ],
    )

    assert check_auth_runtime.main() == 1
    captured = capsys.readouterr()
    assert "нужен терминал со скрытым вводом пароля" in captured.out
    assert "visible fallback" not in captured.out + captured.err
