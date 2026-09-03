# tests/architecture/test_normative_sidebar_layout.py

"""Architecture guards нового layout нормативной панели."""

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

GLOBAL_CSS = FRONTEND_SOURCE / "css" / "global.css"

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


def test_normative_selection_is_hidden_but_preserved() -> None:
    """Пользовательские normative checkbox скрыты через общий state class."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    catalog = CATALOG_JS.read_text(
        encoding="utf-8",
    )

    global_css = GLOBAL_CSS.read_text(
        encoding="utf-8",
    )

    assert "data-normative-admin-selection" in html

    assert "normative-sidebar__toolbar is-hidden" in html

    assert catalog.count('"normative-sidebar__checkbox is-hidden"') >= 2

    assert ".is-hidden" in global_css

    assert 'class="hidden"' not in html


def test_new_ready_normatives_are_selected_automatically() -> None:
    """READY нормативы автоматически входят в пользовательский snapshot."""
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

    assignment_position = catalog.find("state.documents = documents")

    selection_position = catalog.find(
        "selectNewReadyDocumentsByDefault()",
        assignment_position,
    )

    assert assignment_position >= 0

    assert selection_position > assignment_position


def test_tz_and_user_packages_have_safe_scaffolding() -> None:
    """ТЗ недоступно, а packages имеют отдельный будущий DOM API."""
    html = INDEX_HTML.read_text(
        encoding="utf-8",
    )

    required = (
        "data-specification-placeholder",
        'aria-disabled="true"',
        "data-user-packages-block",
        "data-user-packages-select-all",
        "data-user-packages-clear",
        "data-user-package-create",
        "data-user-packages-placeholder",
    )

    missing = [marker for marker in required if marker not in html]

    assert not missing, "\n".join(
        missing,
    )

    specification_start = html.find("data-specification-placeholder")

    specification_end = html.find(
        "</section>",
        specification_start,
    )

    specification_markup = html[specification_start:specification_end]

    assert "disabled" in specification_markup


def test_frontend_static_assets_are_not_mixed_between_deploys() -> None:
    """HTML/JS/CSS не должны оставаться stale после нового deploy."""
    content = NGINX_CONFIG.read_text(
        encoding="utf-8",
    )

    assert "location = /index.html" in content

    assert r"location ~* \.(?:css|js)$" in content

    assert "no-store, no-cache, must-revalidate, max-age=0" in content

    assert "location ^~ /api/" in content
