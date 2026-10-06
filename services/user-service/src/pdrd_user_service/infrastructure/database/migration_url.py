# services/user-service/src/pdrd_user_service/infrastructure/database/migration_url.py

"""Определение PostgreSQL URL миграций без вывода реквизитов в ошибки."""

import os

from pdrd_user_service.core.settings import Settings, get_settings
from pdrd_user_service.infrastructure.database.engine import build_database_url
from sqlalchemy.engine import make_url


def _validate_url(value: str) -> str:
    """Принимает только полный URL PostgreSQL с драйвером asyncpg."""
    try:
        parsed = make_url(value)
    except Exception:
        raise ValueError("Некорректный URL PostgreSQL для User Service.") from None
    if (
        parsed.drivername != "postgresql+asyncpg"
        or not parsed.host
        or not parsed.database
        or not parsed.username
        or not parsed.password
    ):
        raise ValueError("Миграции User Service требуют полный PostgreSQL URL.")
    return parsed.render_as_string(hide_password=False)


def resolve_migration_url(
    *, settings: Settings | None = None, explicit_url: str | None = None
) -> str:
    """Использует ровно один источник настроек и не подключается к БД."""
    override = (
        explicit_url
        if explicit_url is not None
        else os.environ.get("USER_SERVICE_DATABASE_URL")
    )
    if override:
        if any(
            name.upper().startswith("USER_SERVICE_DATABASE__") for name in os.environ
        ):
            raise ValueError(
                "Нельзя одновременно задавать USER_SERVICE_DATABASE_URL "
                "и USER_SERVICE_DATABASE__*."
            )
        return _validate_url(override)
    actual_settings = settings if settings is not None else get_settings()
    if actual_settings.database.password.get_secret_value() in {"", "change-me"}:
        raise RuntimeError("Для миграции требуется заданный пароль PostgreSQL.")
    return _validate_url(
        build_database_url(actual_settings.database).render_as_string(
            hide_password=False
        )
    )
