# tests/architecture/test_layer_dependencies.py

"""Архитектурные тесты направления зависимостей backend-слоёв."""

import ast
from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

BACKEND_PACKAGES = (
    (
        "api-gateway",
        "pdrd_api_gateway",
    ),
    (
        "document-service",
        "pdrd_document_service",
    ),
    (
        "knowledge-service",
        "pdrd_knowledge_service",
    ),
    (
        "analysis-service",
        "pdrd_analysis_service",
    ),
)

FRAMEWORK_IMPORTS = (
    "asyncpg",
    "celery",
    "ezdxf",
    "fastapi",
    "fitz",
    "httpx",
    "PIL",
    "pydantic",
    "sqlalchemy",
)


def package_directory(
    service_name: str,
    package_name: str,
) -> Path:
    """Возвращает src package backend-сервиса."""
    return REPOSITORY_ROOT / "services" / service_name / "src" / package_name


def iter_import_names(
    source_file: Path,
) -> list[str]:
    """Возвращает imported module names."""
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
        directory.rglob(
            "*.py",
        )
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
    """Проверяет Domain всех backend-сервисов."""
    violations: list[str] = []

    for (
        service_name,
        package_name,
    ) in BACKEND_PACKAGES:
        package = package_directory(
            service_name,
            package_name,
        )

        forbidden_imports = (
            *FRAMEWORK_IMPORTS,
            f"{package_name}.application",
            f"{package_name}.infrastructure",
            f"{package_name}.transport",
        )

        violations.extend(
            find_forbidden_imports(
                package / "domain",
                forbidden_imports,
            )
        )

    assert not violations, "\n".join(
        violations,
    )


def test_application_does_not_depend_on_infrastructure() -> None:
    """Проверяет Application всех backend-сервисов."""
    violations: list[str] = []

    for (
        service_name,
        package_name,
    ) in BACKEND_PACKAGES:
        package = package_directory(
            service_name,
            package_name,
        )

        forbidden_imports = (
            *FRAMEWORK_IMPORTS,
            f"{package_name}.infrastructure",
            f"{package_name}.transport",
        )

        violations.extend(
            find_forbidden_imports(
                package / "application",
                forbidden_imports,
            )
        )

    assert not violations, "\n".join(
        violations,
    )
