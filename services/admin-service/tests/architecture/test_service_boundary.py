# services/admin-service/tests/architecture/test_service_boundary.py

"""Защищает границу admin-service от прямого доступа к данным других сервисов."""

import ast
from pathlib import Path


def test_admin_service_does_not_import_other_service_or_database_driver() -> None:
    """Админка оркестрирует HTTP API, а не импортирует чужую БД или домен."""
    package = Path(__file__).resolve().parents[2] / "src" / "pdrd_admin_service"
    banned = (
        "pdrd_user_service",
        "pdrd_auth_service",
        "sqlalchemy",
        "asyncpg",
        "psycopg",
    )
    checked = 0
    for source in package.rglob("*.py"):
        checked += 1
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(module.startswith(banned) for module in imports), (
            source.relative_to(package)
        )
    assert checked >= 10
