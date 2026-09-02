# tests/architecture/test_frontend_structure.py

"""Architecture tests структуры vanilla frontend."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"

FRONTEND_SOURCE = FRONTEND_ROOT / "src"

REQUIRED_FILES = (
    "index.html",
    "css/variables.css",
    "css/global.css",
    "css/main.css",
    "css/blocks/card.css",
    "css/blocks/form.css",
    "css/blocks/modal.css",
    "css/blocks/report.css",
    "js/app.js",
    "js/config.js",
    "js/components/modal.js",
    "js/components/result.js",
    "js/features/analysis/api.js",
    "js/features/analysis/form.js",
    "js/features/analysis/labels.js",
    "js/features/analysis/polling.js",
    "js/features/analysis/report.js",
)

FORBIDDEN_LEGACY_FILES = (
    FRONTEND_ROOT / "app.js",
    FRONTEND_ROOT / "styles.css",
    FRONTEND_ROOT / "index.html",
)

FORBIDDEN_RUNTIME_MARKERS = (
    "pdrd-pdf-service",
    ":8101",
    "/webhook/",
)


def test_frontend_uses_src_structure() -> None:
    """Проверяет обязательную frontend layout."""
    missing = [
        relative_path
        for relative_path in REQUIRED_FILES
        if not (FRONTEND_SOURCE / relative_path).is_file()
    ]

    assert not missing, "Отсутствуют frontend source files:\n" + "\n".join(
        missing,
    )


def test_frontend_has_no_root_legacy_files() -> None:
    """Не допускает возврат старых frontend monolith files."""
    existing = [
        path.relative_to(
            REPOSITORY_ROOT,
        ).as_posix()
        for path in FORBIDDEN_LEGACY_FILES
        if path.exists()
    ]

    assert not existing, "Найдены legacy frontend files:\n" + "\n".join(
        existing,
    )


def test_frontend_source_headers_match_paths() -> None:
    """Проверяет relative-path header JS/CSS/HTML."""
    violations: list[str] = []

    for path in sorted(
        FRONTEND_SOURCE.rglob(
            "*",
        )
    ):
        if not path.is_file() or path.suffix not in {
            ".js",
            ".css",
            ".html",
        }:
            continue

        relative_path = path.relative_to(
            REPOSITORY_ROOT,
        ).as_posix()

        first_line = path.read_text(
            encoding="utf-8",
        ).splitlines()

        actual = first_line[0] if first_line else ""

        if path.suffix == ".js":
            expected = f"// {relative_path}"

        elif path.suffix == ".css":
            expected = f"/* {relative_path} */"

        else:
            expected = f"<!-- {relative_path} -->"

        if actual != expected:
            violations.append(
                f"{relative_path}: expected={expected!r}; actual={actual!r}"
            )

    assert not violations, "\n".join(
        violations,
    )


def test_css_layer_order() -> None:
    """Проверяет variables -> global -> blocks."""
    content = (FRONTEND_SOURCE / "css" / "main.css").read_text(
        encoding="utf-8",
    )

    variables_position = content.find(
        "./variables.css",
    )

    global_position = content.find(
        "./global.css",
    )

    first_block_position = content.find(
        "./blocks/",
    )

    assert variables_position >= 0
    assert global_position >= 0
    assert first_block_position >= 0

    assert variables_position < global_position < first_block_position


def test_frontend_has_no_legacy_runtime_route() -> None:
    """Проверяет Browser -> Gateway boundary."""
    files = [
        FRONTEND_ROOT / "nginx.conf",
        *sorted(
            FRONTEND_SOURCE.rglob(
                "*.js",
            )
        ),
        *sorted(
            FRONTEND_SOURCE.rglob(
                "*.html",
            )
        ),
    ]

    violations: list[str] = []

    for path in files:
        content = path.read_text(
            encoding="utf-8",
        )

        for marker in FORBIDDEN_RUNTIME_MARKERS:
            if marker in content:
                violations.append(
                    path.relative_to(
                        REPOSITORY_ROOT,
                    ).as_posix()
                    + f": {marker}"
                )

    assert not violations, "\n".join(
        violations,
    )


def test_frontend_app_is_composition_root() -> None:
    """Не позволяет app.js снова превратиться в god-file."""
    path = FRONTEND_SOURCE / "js" / "app.js"

    line_count = len(
        path.read_text(
            encoding="utf-8",
        ).splitlines()
    )

    assert line_count <= 250, (
        "frontend/src/js/app.js "
        "должен оставаться composition root; "
        f"получено строк: {line_count}."
    )
