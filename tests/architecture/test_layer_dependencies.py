# tests/architecture/test_layer_dependencies.py

"""Архитектурные тесты направления зависимостей backend-слоёв."""

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

API_GATEWAY_PACKAGE = (
    REPOSITORY_ROOT / "services" / "api-gateway" / "src" / "pdrd_api_gateway"
)

DOMAIN_DIRECTORY = API_GATEWAY_PACKAGE / "domain"

APPLICATION_DIRECTORY = API_GATEWAY_PACKAGE / "application"

DOMAIN_FORBIDDEN_IMPORTS = (
    "fastapi",
    "sqlalchemy",
    "celery",
    "asyncpg",
    "pdrd_api_gateway.infrastructure",
    "pdrd_api_gateway.transport",
)

APPLICATION_FORBIDDEN_IMPORTS = (
    "fastapi",
    "sqlalchemy",
    "celery",
    "asyncpg",
    "pdrd_api_gateway.infrastructure",
    "pdrd_api_gateway.transport",
)


def iter_import_names(
    source_file: Path,
) -> list[str]:
    """Возвращает imported module names из Python source."""
    syntax_tree = ast.parse(
        source_file.read_text(
            encoding="utf-8",
        )
    )

    imports: list[str] = []

    for node in ast.walk(
        syntax_tree,
    ):
        if isinstance(
            node,
            ast.Import,
        ):
            imports.extend(alias.name for alias in node.names)

        if (
            isinstance(
                node,
                ast.ImportFrom,
            )
            and node.module is not None
        ):
            imports.append(
                node.module,
            )

    return imports


def find_forbidden_imports(
    directory: Path,
    forbidden_imports: tuple[str, ...],
) -> list[str]:
    """Находит нарушения dependency direction."""
    violations: list[str] = []

    if not directory.exists():
        return violations

    for source_file in sorted(
        directory.rglob("*.py"),
    ):
        for import_name in iter_import_names(
            source_file,
        ):
            if import_name.startswith(
                forbidden_imports,
            ):
                relative_path = source_file.relative_to(
                    REPOSITORY_ROOT,
                )

                violations.append(
                    f"{relative_path.as_posix()}: forbidden import {import_name!r}"
                )

    return violations


def test_domain_does_not_depend_on_frameworks() -> None:
    """Проверяет независимость Domain от frameworks/infrastructure."""
    violations = find_forbidden_imports(
        DOMAIN_DIRECTORY,
        DOMAIN_FORBIDDEN_IMPORTS,
    )

    assert not violations, "\n".join(
        violations,
    )


def test_application_does_not_depend_on_infrastructure() -> None:
    """Проверяет направление Application -> Ports, а не Infrastructure."""
    violations = find_forbidden_imports(
        APPLICATION_DIRECTORY,
        APPLICATION_FORBIDDEN_IMPORTS,
    )

    assert not violations, "\n".join(
        violations,
    )
