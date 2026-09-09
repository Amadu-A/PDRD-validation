# tests/architecture/test_normative_frontend.py

"""Architecture tests managed normative frontend."""

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

CONFIG_JS = FRONTEND_SOURCE / "js" / "config.js"

APP_JS = FRONTEND_SOURCE / "js" / "app.js"

ANALYSIS_FORM_JS = FRONTEND_SOURCE / "js" / "features" / "analysis" / "form.js"

NORMATIVE_API_JS = FRONTEND_SOURCE / "js" / "features" / "normative" / "api.js"

NORMATIVE_CATALOG_JS = FRONTEND_SOURCE / "js" / "features" / "normative" / "catalog.js"

STYLE_CSS = FRONTEND_SOURCE / "css" / "style.css"

NORMATIVE_CSS = FRONTEND_SOURCE / "css" / "blocks" / "normative-sidebar.css"


def test_normative_frontend_files_exist() -> None:
    """Проверяет наличие feature и BEM stylesheet."""
    required = (
        NORMATIVE_API_JS,
        NORMATIVE_CATALOG_JS,
        NORMATIVE_CSS,
    )

    missing = [
        path.relative_to(
            REPOSITORY_ROOT,
        ).as_posix()
        for path in required
        if not path.is_file()
    ]

    assert not missing, "\n".join(
        missing,
    )


def test_normative_sidebar_uses_stable_data_hooks() -> None:
    """Закрепляет DOM API normative sidebar."""
    content = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    required_hooks = (
        "data-normative-sidebar",
        "data-normative-section-select",
        "data-normative-section-create",
        "data-normative-section-rename",
        "data-normative-section-delete",
        "data-normative-category-create",
        "data-normative-select-all",
        "data-normative-clear-all",
        "data-normative-upload-zone",
        "data-normative-file-input",
        "data-normative-tree",
        "data-normative-status",
    )

    missing = [hook for hook in required_hooks if hook not in content]

    assert not missing, "\n".join(
        missing,
    )


def test_normative_frontend_uses_only_gateway_api() -> None:
    """Browser не знает адрес внутреннего Knowledge Service."""
    content = "\n".join(
        (
            CONFIG_JS.read_text(
                encoding="utf-8",
            ),
            NORMATIVE_API_JS.read_text(
                encoding="utf-8",
            ),
            NORMATIVE_CATALOG_JS.read_text(
                encoding="utf-8",
            ),
        )
    )

    assert 'NORMATIVE_ENDPOINT = "/api/v1/normative"' in content

    forbidden = (
        ":8401",
        "pdrd-knowledge-service",
        "/internal/v1/normative",
    )

    violations = [marker for marker in forbidden if marker in content]

    assert not violations, "\n".join(
        violations,
    )


def test_analysis_form_serializes_normative_snapshot_selection() -> None:
    """Selected section/documents входят в analysis multipart."""
    content = ANALYSIS_FORM_JS.read_text(
        encoding="utf-8",
    )

    assert '"normative_section_id"' in content

    assert '"normative_document_ids"' in content

    assert "JSON.stringify" in content

    assert "getNormativeSelection" in content


def test_normative_renderer_does_not_use_inner_html() -> None:
    """Dynamic user names/text не должны попадать в raw innerHTML."""
    content = NORMATIVE_CATALOG_JS.read_text(
        encoding="utf-8",
    )

    assert ".innerHTML" not in content

    assert "textContent" in content

    assert "createElement(" in content


def test_normative_ready_and_drag_drop_contract_is_present() -> None:
    """UI выбирает только READY и поддерживает managed drag/drop."""
    content = NORMATIVE_CATALOG_JS.read_text(
        encoding="utf-8",
    )

    assert 'index_status === "ready"' in content

    assert "ready_for_analysis === true" in content

    assert "checkbox.disabled = !isReady" in content

    assert "dragstart" in content

    assert '"drop"' in content

    assert "moveDocument(" in content

    assert "queueDocument(" in content

    style = STYLE_CSS.read_text(
        encoding="utf-8",
    )

    assert "./blocks/normative-sidebar.css" in style

    app = APP_JS.read_text(
        encoding="utf-8",
    )

    assert "createNormativeCatalog" in app
