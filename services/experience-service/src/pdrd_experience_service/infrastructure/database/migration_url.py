# services/experience-service/src/pdrd_experience_service/infrastructure/database/migration_url.py

"""Определение подключения PostgreSQL для миграций Experience Service.

Назначение файла:
- использовать те же типизированные настройки, что и HTTP-приложение;
- сохранить совместимость с существующими изолированными DB-тестами;
- исключить неоднозначность при одновременном задании двух DSN;
- не раскрывать пароль в диагностических сообщениях.

Основной способ:
    EXPERIENCE_SERVICE_DATABASE__HOST
    EXPERIENCE_SERVICE_DATABASE__PORT
    EXPERIENCE_SERVICE_DATABASE__NAME
    EXPERIENCE_SERVICE_DATABASE__USER
    EXPERIENCE_SERVICE_DATABASE__PASSWORD

Совместимый способ для контролируемых миграций:
    EXPERIENCE_SERVICE_DATABASE_URL

Одновременно передавать оба варианта через окружение запрещено.
"""

import os

from sqlalchemy.engine import make_url

from pdrd_experience_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_experience_service.infrastructure.database.engine import (
    build_database_url,
)


def _validate_url(value: str) -> str:
    """Проверяет драйвер и обязательные параметры подключения.

    Не включает исходный URL в сообщения об ошибках, чтобы
    случайно не раскрыть пароль PostgreSQL.
    """
    try:
        parsed = make_url(value)
    except Exception as error:
        raise ValueError("Некорректная строка подключения PostgreSQL.") from error

    if (
        parsed.drivername != "postgresql+asyncpg"
        or not parsed.host
        or not parsed.database
        or not parsed.username
        or not parsed.password
    ):
        raise ValueError(
            "Для миграции требуется полный PostgreSQL URL с драйвером asyncpg."
        )

    return parsed.render_as_string(
        hide_password=False,
    )


def resolve_migration_url(
    *,
    settings: Settings | None = None,
    explicit_url: str | None = None,
) -> str:
    """Возвращает однозначное подключение для Alembic.

    Если передан явный URL, проверяем отсутствие структурированных
    переменных подключения в текущем процессе.

    Если явного URL нет, используем общие Pydantic Settings.
    В этом режиме миграции разрешаются только при явном
    включении Experience Service.

    Само разрешение URL не открывает соединение с PostgreSQL.
    """
    override = (
        explicit_url
        if explicit_url is not None
        else os.environ.get(
            "EXPERIENCE_SERVICE_DATABASE_URL",
        )
    )

    if override:
        conflicting = [
            name
            for name in os.environ
            if name.upper().startswith("EXPERIENCE_SERVICE_DATABASE__")
        ]

        if conflicting:
            raise ValueError(
                "Нельзя одновременно использовать "
                "EXPERIENCE_SERVICE_DATABASE_URL "
                "и структурированные переменные подключения."
            )

        return _validate_url(override)

    actual_settings = settings if settings is not None else get_settings()

    if not actual_settings.enabled:
        raise RuntimeError(
            "Миграция через Pydantic Settings требует "
            "явного включения Experience Service."
        )

    database_url = build_database_url(
        actual_settings.database,
    )

    return _validate_url(
        database_url.render_as_string(
            hide_password=False,
        )
    )
