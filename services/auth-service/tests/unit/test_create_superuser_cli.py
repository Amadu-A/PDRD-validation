# services/auth-service/tests/unit/test_create_superuser_cli.py

"""Проверяет интерактивную CLI без подключения к базе, AD или User Service."""

import sys
from unittest.mock import AsyncMock

import pytest
from pdrd_auth_service import create_superuser


def test_mismatched_password_does_not_touch_runtime(monkeypatch, capsys):
    """Ошибка подтверждения не создаёт учётные данные и не выводит пароль."""
    monkeypatch.setattr(sys, "argv", ["create_superuser", "--username", "admin"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    passwords = iter(["test-local-password", "another-password"])
    monkeypatch.setattr(create_superuser.getpass, "getpass", lambda _: next(passwords))
    run = AsyncMock()
    monkeypatch.setattr(create_superuser, "_run", run)
    assert create_superuser.main() == 1
    run.assert_not_called()
    output = capsys.readouterr()
    assert "не совпадают" in output.err
    assert "test-local-password" not in output.err


def test_cli_requires_tty(monkeypatch):
    """Без терминала getpass не может перейти к видимому чтению stdin."""
    monkeypatch.setattr(sys, "argv", ["create_superuser"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        create_superuser.main()
    assert error.value.code == 1


def test_cli_default_admin_and_confirmation(monkeypatch):
    """Пустое имя выбирает admin; оба ввода пароля совпадают до запуска."""
    monkeypatch.setattr(sys, "argv", ["create_superuser"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(
        create_superuser.getpass, "getpass", lambda _: "test-local-password"
    )
    run = AsyncMock()
    monkeypatch.setattr(create_superuser, "_run", run)
    assert create_superuser.main() == 0
    run.assert_awaited_once_with("admin", "test-local-password")
