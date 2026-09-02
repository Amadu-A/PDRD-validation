# tests/architecture/test_frontend_structure.py

"""
Architecture tests структуры vanilla frontend.

Модуль закрепляет актуальные frontend-инварианты проекта: единый CSS
entrypoint, модульную BEM-структуру, data-* hooks для JavaScript,
deferred module script в head, отсутствие legacy runtime routes
и сохранение app.js в роли composition root.
"""

import re
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
    "css/style.css",
    "css/blocks/page.css",
    "css/blocks/analysis-panel.css",
    "css/blocks/analysis-form.css",
    "css/blocks/analysis-result.css",
    "css/blocks/analysis-modal.css",
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
    FRONTEND_SOURCE / "css" / "main.css",
    FRONTEND_SOURCE / "css" / "blocks" / "card.css",
    FRONTEND_SOURCE / "css" / "blocks" / "form.css",
    FRONTEND_SOURCE / "css" / "blocks" / "report.css",
    FRONTEND_SOURCE / "css" / "blocks" / "modal.css",
)

FORBIDDEN_RUNTIME_MARKERS = (
    "pdrd-pdf-service",
    ":8101",
    "/webhook/",
)

FORBIDDEN_LEGACY_VISUAL_CLASSES = {
    "container",
    "card",
    "context-box",
    "checkbox-label",
    "note-range",
    "report",
    "modal",
    "modal-card",
    "spinner",
    "hidden",
    "muted",
    "required",
}

REQUIRED_DATA_HOOKS = (
    "data-analysis-form",
    "data-pdf-input",
    "data-cad-input",
    "data-pages-input",
    "data-pages-hint",
    "data-explanatory-note-input",
    "data-note-start-input",
    "data-note-end-input",
    "data-submit-button",
    "data-analysis-result",
    "data-analysis-modal",
    "data-analysis-modal-text",
)


def test_frontend_uses_src_structure() -> None:
    """Проверяет наличие обязательной модульной frontend-структуры."""
    missing = [
        relative_path
        for relative_path in REQUIRED_FILES
        if not (FRONTEND_SOURCE / relative_path).is_file()
    ]

    assert not missing, "Отсутствуют frontend source files:\n" + "\n".join(
        missing,
    )


def test_frontend_has_no_legacy_files() -> None:
    """Не допускает возврат удалённых monolith и legacy CSS-файлов."""
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
    """Проверяет обязательный relative-path header JS, CSS и HTML."""
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

        lines = path.read_text(
            encoding="utf-8",
        ).splitlines()

        actual = lines[0] if lines else ""

        if path.suffix == ".js":
            expected = f"// {relative_path}"

        elif path.suffix == ".css":
            expected = f"/* {relative_path} */"

        else:
            expected = f"<!-- {relative_path} -->"

        if actual != expected:
            violations.append(
                f"{relative_path}: "
                f"expected={expected!r}; "
                f"actual={actual!r}"
            )

    assert not violations, "\n".join(
        violations,
    )


def test_css_layer_order() -> None:
    """Проверяет порядок variables -> global -> BEM blocks."""
    content = (
        FRONTEND_SOURCE
        / "css"
        / "style.css"
    ).read_text(
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

    assert (
        variables_position
        < global_position
        < first_block_position
    )


def test_frontend_loads_styles_then_module_in_head() -> None:
    """
    Проверяет CSS entrypoint и deferred module script внутри HTML head.

    Скрипт должен быть объявлен после CSS, чтобы точка подключения
    ресурсов оставалась предсказуемой и не расползалась по body.
    """
    content = (
        FRONTEND_SOURCE
        / "index.html"
    ).read_text(
        encoding="utf-8",
    )

    head_end = content.find(
        "</head>",
    )

    style_position = content.find(
        'href="/css/style.css"',
    )

    script_start = content.find(
        "<script",
    )

    script_end = content.find(
        "</script>",
        script_start,
    )

    assert head_end >= 0
    assert style_position >= 0
    assert script_start >= 0
    assert script_end >= 0

    assert (
        style_position
        < script_start
        < head_end
    )

    script_markup = content[
        script_start:script_end
    ]

    assert 'src="/js/app.js"' in script_markup
    assert 'type="module"' in script_markup
    assert re.search(
        r"\bdefer\b",
        script_markup,
    )


def test_frontend_uses_data_hooks_for_javascript() -> None:
    """
    Закрепляет data-* hooks как стабильный DOM API frontend.

    JavaScript не должен зависеть от визуальных BEM-классов или id,
    которые могут меняться при редизайне.
    """
    html = (
        FRONTEND_SOURCE
        / "index.html"
    ).read_text(
        encoding="utf-8",
    )

    missing_hooks = [
        hook
        for hook in REQUIRED_DATA_HOOKS
        if hook not in html
    ]

    assert not missing_hooks, (
        "Отсутствуют обязательные frontend data hooks:\n"
        + "\n".join(
            missing_hooks,
        )
    )

    app_js = (
        FRONTEND_SOURCE
        / "js"
        / "app.js"
    ).read_text(
        encoding="utf-8",
    )

    assert "getElementById(" not in app_js


def test_frontend_has_no_legacy_visual_classes() -> None:
    """Не допускает возврат прежних не-BEM visual class names."""
    html = (
        FRONTEND_SOURCE
        / "index.html"
    ).read_text(
        encoding="utf-8",
    )

    class_values = re.findall(
        r'class="([^"]+)"',
        html,
    )

    actual_classes = {
        class_name
        for value in class_values
        for class_name in value.split()
    }

    violations = sorted(
        actual_classes
        & FORBIDDEN_LEGACY_VISUAL_CLASSES
    )

    assert not violations, (
        "Найдены legacy visual classes:\n"
        + "\n".join(
            violations,
        )
    )


def test_frontend_has_no_legacy_runtime_route() -> None:
    """Проверяет обязательную Browser -> API Gateway boundary."""
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
    """Не позволяет app.js снова превратиться в frontend god-file."""
    path = (
        FRONTEND_SOURCE
        / "js"
        / "app.js"
    )

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
