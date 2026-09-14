# tests/architecture/test_normative_sidebar_interactions.py

"""Architecture guards интерактивности managed normative sidebar."""

from pathlib import Path

REPOSITORY_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

FRONTEND_SOURCE = REPOSITORY_ROOT / "frontend" / "src"

INDEX_HTML = FRONTEND_SOURCE / "index.html"

NORMATIVE_CSS = FRONTEND_SOURCE / "css" / "blocks" / "normative-sidebar.css"

TECHNICAL_ASSIGNMENT_JS = (
    FRONTEND_SOURCE / "js" / "features" / "technical_assignment" / "file.js"
)

PROMPT_JS = FRONTEND_SOURCE / "js" / "features" / "normative" / "prompt.js"


def test_technical_assignment_has_drag_drop_and_clickable_clear() -> None:
    """ТЗ использует drop-zone и отдельную доступную кнопку очистки."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    controller = TECHNICAL_ASSIGNMENT_JS.read_text(
        encoding="utf-8",
    )

    required_html = (
        "data-technical-assignment-upload-zone",
        "data-technical-assignment-clear",
        "Перетащите файл сюда или нажмите для выбора.",
    )

    missing_html = [marker for marker in required_html if marker not in html]

    assert not missing_html, "\n".join(
        missing_html,
    )

    required_controller = (
        '"dragenter"',
        '"dragover"',
        '"dragleave"',
        '"drop"',
        "DataTransfer",
        "SUPPORTED_EXTENSIONS",
        "clearButton.addEventListener",
    )

    missing_controller = [
        marker for marker in required_controller if marker not in controller
    ]

    assert not missing_controller, "\n".join(
        missing_controller,
    )

    assert ".innerHTML" not in controller


def test_normative_and_user_package_blocks_are_native_accordions() -> None:
    """Оба длинных каталога можно свернуть до одного заголовка."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    css = NORMATIVE_CSS.read_text(
        encoding="utf-8",
    )

    required_html = (
        "data-normative-documents-accordion",
        "data-user-packages-accordion",
        'class="normative-sidebar__accordion-summary"',
        'class="normative-sidebar__accordion-content"',
    )

    missing_html = [marker for marker in required_html if marker not in html]

    assert not missing_html, "\n".join(
        missing_html,
    )

    assert html.count("<details") >= 2
    assert html.count("<summary") >= 2

    assert ".normative-sidebar__accordion-summary" in css
    assert ".normative-sidebar__accordion-content" in css
    assert ".normative-sidebar__accordion[open]" in css


def test_user_package_tree_is_horizontally_bounded() -> None:
    """Длинные package/document names не расширяют sidebar."""
    css = NORMATIVE_CSS.read_text(
        encoding="utf-8",
    )

    required = (
        ".normative-sidebar__category-children",
        ".normative-sidebar__document-content",
        ".normative-sidebar__document-link",
        "text-overflow: ellipsis;",
        "overflow: hidden;",
        "max-width: 100%;",
    )

    missing = [marker for marker in required if marker not in css]

    assert not missing, "\n".join(
        missing,
    )


def test_prompt_title_opens_large_synchronized_editor() -> None:
    """Working prompt имеет modal editor без отдельного состояния текста."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    controller = PROMPT_JS.read_text(
        encoding="utf-8",
    )

    required_html = (
        "data-normative-prompt-open",
        "data-normative-prompt-dialog",
        "data-normative-prompt-dialog-textarea",
        "data-normative-prompt-dialog-save",
        "data-normative-prompt-dialog-restore",
        "data-normative-prompt-dialog-close",
        "data-normative-prompt-dialog-status",
    )

    missing_html = [marker for marker in required_html if marker not in html]

    assert not missing_html, "\n".join(
        missing_html,
    )

    required_controller = (
        "syncPromptValues",
        "dialogTextarea",
        "dialog.showModal",
        "dialog.close",
        "updateWorkingPrompt",
    )

    missing_controller = [
        marker for marker in required_controller if marker not in controller
    ]

    assert not missing_controller, "\n".join(
        missing_controller,
    )

    assert ".innerHTML" not in controller
