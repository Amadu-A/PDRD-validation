# services/auth-service/tests/unit/test_https_security.py

"""Не допускает HTTP и отключения Secure cookie в рабочих окружениях."""

import pytest
from pdrd_auth_service.core.settings import Settings
from pydantic import ValidationError


@pytest.mark.parametrize("environment", ["stage", "prod"])
@pytest.mark.parametrize(
    ("origin", "secure"),
    [
        ("http://192.168.55.3:8080", True),
        ("http://192.168.55.3:8080", False),
        ("https://pdrd.itcneoterm.local", False),
    ],
)
def test_working_auth_requires_https_and_secure_cookie(environment, origin, secure):
    """Рабочий режим отклоняет небезопасную конфигурацию до сборки runtime."""
    with pytest.raises(ValidationError, match=r"HTTPS|Secure"):
        Settings(
            _env_file=None,
            environment=environment,
            database={"password": "test-database-password"},
            http={
                "enabled": True,
                "public_origin": origin,
                "cookie_secure": secure,
                "internal_key": "i" * 32,
                "csrf_key": "c" * 32,
                "user_service_internal_key": "u" * 32,
            },
        )
