# tests/architecture/test_source_file_headers.py

"""Архитектурный тест обязательного комментария с относительным путём файла.

Проверка распространяется на Python-код новых микросервисов и защищает
принятый project code-style от появления файлов без однозначно указанного
расположения в repository.
"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

SERVICE_SOURCE_DIRECTORIES = (
    REPOSITORY_ROOT / "services" / "api-gateway" / "src",
    REPOSITORY_ROOT / "services" / "document-service" / "src",
    REPOSITORY_ROOT / "services" / "knowledge-service" / "src",
    REPOSITORY_ROOT / "services" / "analysis-service" / "src",
)


def iter_python_source_files() -> list[Path]:
    """Возвращает Python-файлы новых микросервисов.

    Returns:
        Отсортированный список Python-файлов внутри новых src-каталогов.
    """
    source_files: list[Path] = []

    for source_directory in SERVICE_SOURCE_DIRECTORIES:
        if not source_directory.exists():
            continue

        source_files.extend(
            path
            for path in source_directory.rglob("*.py")
            if "__pycache__" not in path.parts
        )

    return sorted(source_files)


def test_python_sources_have_relative_path_header() -> None:
    """Проверяет первую строку каждого Python-файла нового backend.

    Первая строка должна в точности содержать комментарий с относительным
    путём от корня repository. Это позволяет автоматически контролировать
    одно из обязательных правил project code-style.
    """
    violations: list[str] = []

    for source_file in iter_python_source_files():
        relative_path = source_file.relative_to(REPOSITORY_ROOT).as_posix()
        expected_header = f"# {relative_path}"

        lines = source_file.read_text(encoding="utf-8").splitlines()
        actual_header = lines[0] if lines else ""

        if actual_header != expected_header:
            violations.append(
                f"{relative_path}: ожидается первая строка "
                f"{expected_header!r}, получено {actual_header!r}"
            )

    assert not violations, "\n".join(violations)
