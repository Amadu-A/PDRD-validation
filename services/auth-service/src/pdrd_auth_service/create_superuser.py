# services/auth-service/src/pdrd_auth_service/create_superuser.py

"""Интерактивная серверная команда создания первого суперпользователя.

Пароль вводится через TTY дважды и не передаётся в аргументах, переменных
окружения или User Service. Незавершённый bootstrap можно повторить.
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy.exc import SQLAlchemyError

from pdrd_auth_service.core.runtime import build_runtime
from pdrd_auth_service.core.settings import get_settings
from pdrd_auth_service.domain.local_credential import normalize_username
from pdrd_auth_service.domain.passwords import validate_password
from pdrd_auth_service.infrastructure.user_service import UserServiceUnavailable


async def _run(username: str, password: str) -> None:
    """Создаёт профиль через runtime и гарантированно закрывает пулы."""
    runtime = build_runtime(get_settings())
    try:
        user_id = await runtime.local.create_superuser(
            username=username, password=password
        )
        print(f"Суперпользователь {username} создан: {user_id}")
    finally:
        await runtime.close()


def main() -> int:
    """Запрашивает имя и скрытый пароль с подтверждением без аргумента password."""
    parser = argparse.ArgumentParser(
        description="Создать первого локального суперпользователя PDRD"
    )
    parser.add_argument("--username", help="Имя пользователя; по умолчанию admin")
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        parser.exit(
            1, "Нужен интерактивный терминал: bash scripts/create-superuser.sh\n"
        )
    try:
        username = normalize_username(
            args.username or input("Имя пользователя [admin]: ") or "admin"
        )
        password = getpass.getpass("Пароль: ")
        confirmation = getpass.getpass("Пароль повторно: ")
        if password != confirmation:
            raise ValueError("Пароли не совпадают")
        validate_password(password)
        asyncio.run(_run(username, password))
    except (ValueError, UserServiceUnavailable) as error:
        print(f"Создание не выполнено: {error}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("База недоступна; проверьте миграции identity и auth.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nСоздание отменено.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
