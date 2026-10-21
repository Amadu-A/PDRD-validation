# tests/architecture/test_frontend_static_delivery.py

"""Сборка и readiness проверяют статику, от которой зависит запуск авторизации и Review."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_image_normalizes_permissions_and_checks_unprivileged_readability():
    """Права checkout не должны закрывать изменённые модули от nginx worker."""
    dockerfile = (ROOT / "frontend/Dockerfile").read_text("utf-8")
    assert (
        dockerfile.index("COPY src")
        < dockerfile.index("chmod 755")
        < dockerfile.index("chmod 644")
    )
    assert "su -s /bin/sh nginx" in dockerfile and "test -r" in dockerfile
    assert "chmod 777" not in dockerfile
    startup = (ROOT / "scripts/up.sh").read_text("utf-8")
    review_update = startup.split("docker compose --profile review up", 1)[1].split(
        "review-frontend", 1
    )[0]
    assert "--build" in review_update
    readiness = (ROOT / "scripts/check-stack.sh").read_text("utf-8")
    for path in (
        "js/features/analysis/visualization.js",
        "js/features/review/controller.js",
        "css/blocks/review-resize.css",
        "js/features/auth/session.js",
    ):
        assert path in dockerfile and path in readiness
    assert 'check_http_status "Frontend asset ${asset}"' in readiness


def test_static_module_dependencies_are_in_the_copied_source_tree():
    """Новые зависимости Review/визуализации должны попасть в тот же образ, что и app.js."""
    base = ROOT / "frontend/src"
    for path in base.rglob("*.js"):
        for source in re.findall(
            r'(?:from\s*|import\s*)["\']([^"\']+)["\']', path.read_text("utf-8")
        ):
            if source.startswith("."):
                assert (path.parent / source).resolve().is_file(), (path, source)
