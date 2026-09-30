# services/user-service/src/pdrd_user_service/bootstrap_admin.py

"""Серверная CLI команда первичного администратора; HTTP маршрута нет.

Запускать однократно после появления подтверждённого корпоративного профиля:
python -m pdrd_user_service.bootstrap_admin --user-id <UUID>
"""

import argparse
import asyncio
from uuid import UUID

from pdrd_user_service.application.ports.repository import (
    AuthorizationConflict,
    BootstrapAlreadyPerformed,
)
from pdrd_user_service.application.use_cases.bootstrap import bootstrap_first_admin
from pdrd_user_service.application.use_cases.users import UserNotFound
from pdrd_user_service.core.settings import get_settings
from pdrd_user_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_user_service.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


async def _run(user_id: UUID) -> UUID:
    """Выполняет единственную защищённую транзакцию и закрывает пул."""
    settings = get_settings()
    if not settings.enabled:
        raise ValueError("Для bootstrap включите USER_SERVICE_ENABLED")
    engine = build_async_engine(settings.database)
    try:
        factory = build_session_factory(engine)
        assignment = await bootstrap_first_admin(
            lambda: SqlAlchemyUnitOfWork(factory), user_id
        )
        return assignment.assignment_id
    finally:
        await engine.dispose()


def main() -> int:
    """Принимает только UUID профиля и сообщает результат без секретов."""
    parser = argparse.ArgumentParser(
        description="Однократно назначить первого администратора PDRD"
    )
    parser.add_argument("--user-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        assignment_id = asyncio.run(_run(args.user_id))
    except (
        AuthorizationConflict,
        BootstrapAlreadyPerformed,
        UserNotFound,
        ValueError,
    ) as error:
        parser.exit(1, f"Bootstrap не выполнен: {error}\n")
    print(f"Первый администратор назначен: {assignment_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
