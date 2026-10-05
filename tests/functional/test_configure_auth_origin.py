# tests/functional/test_configure_auth_origin.py

"""Проверяет настройку HTTPS-адреса без потери секретов или отключения Secure."""

from pathlib import Path

import pytest

from scripts.configure_auth_origin import configure_env, origin_settings


def test_https_origin_update_preserves_private_environment(tmp_path: Path):
    """Адреса согласуются атомарно; остальные настройки сохраняются без вывода."""
    path = tmp_path / ".env"
    original = (
        "# Закрытая настройка\n"
        "SMTP_PASSWORD='test-private-value'\n"
        "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN=http://192.168.55.3:8080\n"
        "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN=https://duplicate.invalid\n"
        "AUTH_SERVICE_HTTP__COOKIE_SECURE=false\n"
    )
    path.write_text(original, encoding="utf-8")
    values = origin_settings("https://pdrd.itcneoterm.local")
    configure_env(path, values)
    content = path.read_text(encoding="utf-8")
    assert "SMTP_PASSWORD='test-private-value'" in content
    assert content.count("AUTH_SERVICE_HTTP__PUBLIC_ORIGIN=") == 1
    assert "AUTH_SERVICE_HTTP__COOKIE_SECURE=true" in content
    for name in (
        "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN",
        "AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL",
        "API_GATEWAY_IDENTITY_PROXY__PUBLIC_ORIGIN",
    ):
        assert f"{name}=https://pdrd.itcneoterm.local" in content
    configure_env(path, values)
    assert path.read_text(encoding="utf-8") == content
    assert not list(tmp_path.glob(".env.origin-*"))


@pytest.mark.parametrize(
    "origin",
    [
        "http://192.168.55.3:8080",
        "http://127.0.0.1:8080",
        "http://pdrd.itcneoterm.local",
        "https://user:password@example.test",
        "https://pdrd.itcneoterm.local/path",
        "https://pdrd.itcneoterm.local?x=1",
        "https://pdrd.itcneoterm.local:99999",
        "https://pdrd.itcneoterm.local\n",
    ],
)
def test_origin_update_rejects_http_and_invalid_addresses(origin):
    """HTTP, встроенные пароли, пути и некорректные адреса не изменяют .env."""
    with pytest.raises(ValueError):
        origin_settings(origin)
