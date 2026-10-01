# services/auth-service/src/pdrd_auth_service/infrastructure/database/migration_url.py

"""Определяет URL миграции без вывода реквизитов PostgreSQL в ошибку."""

import os

from sqlalchemy.engine import make_url

from pdrd_auth_service.core.settings import Settings, get_settings
from pdrd_auth_service.infrastructure.database.engine import build_database_url


def _validate_url(value: str) -> str:
    """Принимает только полный PostgreSQL URL с драйвером asyncpg."""
    try:
        parsed = make_url(value)
    except Exception:
        raise ValueError("Некорректный URL PostgreSQL Auth Service") from None
    if (
        parsed.drivername != "postgresql+asyncpg"
        or not parsed.host
        or not parsed.database
        or not parsed.username
        or not parsed.password
    ):
        raise ValueError("Миграции Auth Service требуют полный PostgreSQL URL")
    return parsed.render_as_string(hide_password=False)


def resolve_migration_url(
    *, settings: Settings | None = None, explicit_url: str | None = None
) -> str:
    """Выбирает один источник настроек без подключения к БД или AD."""
    override = (
        explicit_url
        if explicit_url is not None
        else os.environ.get("AUTH_SERVICE_DATABASE_URL")
    )
    if override:
        if any(
            name.upper().startswith("AUTH_SERVICE_DATABASE__") for name in os.environ
        ):
            raise ValueError(
                "Нельзя одновременно задавать AUTH_SERVICE_DATABASE_URL "
                "и AUTH_SERVICE_DATABASE__*"
            )
        return _validate_url(override)
    actual_settings = settings if settings is not None else get_settings()
    if actual_settings.database.password.get_secret_value() in {"", "change-me"}:
        raise RuntimeError("Для миграции требуется пароль PostgreSQL Auth Service")
    return _validate_url(
        build_database_url(actual_settings.database).render_as_string(
            hide_password=False
        )
    )
