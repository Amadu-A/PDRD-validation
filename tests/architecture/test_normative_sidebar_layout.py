# tests/architecture/test_normative_sidebar_layout.py

"""Architecture guards layout managed catalog sidebar."""

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

INDEX_HTML = FRONTEND_SOURCE / "index.html"

CATALOG_JS = FRONTEND_SOURCE / "js" / "features" / "normative" / "catalog.js"

USER_PACKAGES_JS = (
    FRONTEND_SOURCE / "js" / "features" / "normative" / "user_packages.js"
)

GLOBAL_CSS = FRONTEND_SOURCE / "css" / "global.css"

NORMATIVE_CSS = FRONTEND_SOURCE / "css" / "blocks" / "normative-sidebar.css"

NGINX_CONFIG = FRONTEND_ROOT / "nginx.conf"


def test_sidebar_blocks_have_requested_order() -> None:
    """Закрепляет порядок section -> TZ -> norms -> packages -> prompt."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    markers = (
        "data-normative-section-create",
        "data-normative-section-select",
        "data-specification-placeholder",
        "data-normative-documents-block",
        "data-user-packages-block",
        "data-normative-prompt-block",
    )

    positions = [
        html.find(
            marker,
        )
        for marker in markers
    ]

    assert all(position >= 0 for position in positions)

    assert positions == sorted(
        positions,
    )


def test_normative_selection_is_hidden_but_catalog_remains_visible() -> None:
    """Скрываются checkbox/toolbar, но не documents block и не tree."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    catalog = CATALOG_JS.read_text(
        encoding="utf-8",
    )

    global_css = GLOBAL_CSS.read_text(
        encoding="utf-8",
    )

    sidebar_css = NORMATIVE_CSS.read_text(
        encoding="utf-8",
    )

    assert "data-normative-admin-selection" in html

    assert "normative-sidebar__toolbar is-hidden" in html

    assert catalog.count('"normative-sidebar__checkbox is-hidden"') >= 2

    assert ".is-hidden" in global_css

    assert 'class="hidden"' not in html

    documents_marker = html.find(
        "data-normative-documents-block",
    )

    documents_start = html.rfind(
        "<section",
        0,
        documents_marker,
    )

    documents_end = html.find(
        ">",
        documents_marker,
    )

    documents_opening = html[documents_start:documents_end]

    assert documents_marker >= 0

    assert "is-hidden" not in documents_opening

    tree_marker = html.find(
        "data-normative-tree",
    )

    tree_start = html.rfind(
        "<div",
        0,
        tree_marker,
    )

    tree_end = html.find(
        ">",
        tree_marker,
    )

    tree_opening = html[tree_start:tree_end]

    assert tree_marker >= 0

    assert "is-hidden" not in tree_opening

    assert "overflow-x: hidden;" in sidebar_css

    assert "overflow-y: auto;" in sidebar_css

    assert "flex: 0 0 auto;" in sidebar_css

    assert "overflow: visible;" in sidebar_css


def test_new_ready_normatives_are_selected_automatically() -> None:
    """READY нормативы автоматически входят в normative snapshot."""
    catalog = CATALOG_JS.read_text(
        encoding="utf-8",
    )

    required = (
        "readySeenBySection",
        "selectNewReadyDocumentsByDefault",
        "previouslyReady",
        "currentlyReady",
        "selected.add(",
    )

    missing = [marker for marker in required if marker not in catalog]

    assert not missing, "\n".join(
        missing,
    )

    assignment_position = catalog.find(
        "state.documents = documents",
    )

    selection_position = catalog.find(
        "selectNewReadyDocumentsByDefault()",
        assignment_position,
    )

    assert assignment_position >= 0

    assert selection_position > assignment_position


def test_tz_disabled_and_user_packages_are_live() -> None:
    """ТЗ остаётся disabled, package block получает рабочий DOM API."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    required = (
        "data-specification-placeholder",
        "data-user-packages-block",
        "data-user-packages-select-all",
        "data-user-packages-clear",
        "data-user-package-create",
        "data-user-package-upload-zone",
        "data-user-package-file-input",
        "data-user-packages-tree",
        "data-user-packages-status",
    )

    missing = [marker for marker in required if marker not in html]

    assert not missing, "\n".join(
        missing,
    )

    assert "data-user-packages-placeholder" not in html

    specification_start = html.find(
        "data-specification-placeholder",
    )

    specification_end = html.find(
        "</section>",
        specification_start,
    )

    specification_markup = html[specification_start:specification_end]

    assert 'aria-disabled="true"' in specification_markup

    assert "disabled" in specification_markup

    package_controller = USER_PACKAGES_JS.read_text(
        encoding="utf-8",
    )

    assert '"normative-sidebar__checkbox"' in package_controller

    assert '"normative-sidebar__checkbox is-hidden"' not in package_controller


def test_frontend_static_assets_are_not_mixed_between_deploys() -> None:
    """HTML/JS/CSS не должны оставаться stale после deploy."""
    content = NGINX_CONFIG.read_text(
        encoding="utf-8",
    )

    assert "location = /index.html" in content

    assert r"location ~* \.(?:css|js)$" in content

    assert "no-store, no-cache, must-revalidate, max-age=0" in content

    assert "location ^~ /api/" in content
