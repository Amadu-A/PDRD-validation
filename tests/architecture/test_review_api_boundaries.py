# tests/architecture/test_review_api_boundaries.py

"""Границы отдельного Experience, закрытого фронта, контекста и API-контрактов."""

import ast
from pathlib import Path

from pdrd_api_gateway.transport.http.schemas.review import (
    ReviewCommand as GatewayCommand,
)
from pdrd_experience_service.transport.http.schemas.review import (
    ReviewCommand as ExperienceCommand,
)
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[2]


def test_services_do_not_import_each_others_runtime() -> None:
    """Обмен идёт через порты/HTTP, а не через импорт чужого домена или репозитория."""
    for service, foreign in (
        ("api-gateway", "pdrd_experience_service"),
        ("experience-service", "pdrd_api_gateway"),
    ):
        for source in (ROOT / "services" / service / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith(foreign)
                if isinstance(node, ast.Import):
                    assert all(not name.name.startswith(foreign) for name in node.names)


def test_both_http_boundaries_preserve_the_same_strict_commands() -> None:
    """Изменение публичной команды требует изменения закрытого контракта Experience."""
    assert (
        TypeAdapter(GatewayCommand).json_schema()
        == TypeAdapter(ExperienceCommand).json_schema()
    )


def test_server_deployment_does_not_publish_experience_or_identity_to_browser() -> None:
    """Review основного UI использует nginx-ключ; Experience остаётся в app-net."""
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    experience = compose.split("\n  experience-service:\n", 1)[1].split(
        "\n  review-frontend:\n", 1
    )[0]
    assert "ports:" not in experience
    assert "ai-shared" not in experience
    assert "profiles: [review]" in experience
    private_ui = compose.split("\n  review-frontend:\n", 1)[1].split(
        "\n  frontend:\n", 1
    )[0]
    assert '"127.0.0.1:${REVIEW_FRONTEND_PORT:-8081}:80"' in private_ui
    normal_ui = compose.split("\n  frontend:\n", 1)[1].split("\nvolumes:", 1)[0]
    assert "PDRD_REVIEW_PROXY_KEY: ${API_GATEWAY_REVIEW__UI_KEY:-}" in normal_ui
    assert '"${FRONTEND_BIND_IP:-127.0.0.1}:${FRONTEND_PORT:-8080}:80"' in normal_ui
    nginx = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    assert 'proxy_set_header X-PDRD-Review-Key "${PDRD_REVIEW_PROXY_KEY}"' in nginx
    for source in (ROOT / "frontend/src/js").rglob("*.js"):
        text = source.read_text(encoding="utf-8")
        assert "PDRD_REVIEW_PROXY_KEY" not in text
        assert "X-PDRD-Review-Key" not in text
    review = ROOT / "frontend/src/js/features/review"
    for filename in ("commands.js", "sync.js"):
        text = (review / filename).read_text(encoding="utf-8")
        assert "document." not in text
        assert "fetch(" not in text
