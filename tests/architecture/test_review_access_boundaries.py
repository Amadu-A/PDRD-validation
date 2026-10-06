# tests/architecture/test_review_access_boundaries.py

"""Доступ к ревью остаётся назначением User Service, отдельным от пароля и роли."""

import ast
from pathlib import Path

from pdrd_admin_service.contracts.review_access_models import (
    ChangeReviewAccessRequest as AdminCommand,
)
from pdrd_user_service.transport.http.review_access_schemas import (
    ChangeReviewAccessRequest as UserCommand,
)
from pdrd_user_service.transport.http.schemas import ProvisionRequest, UserResponse

ROOT = Path(__file__).resolve().parents[2]


def test_review_command_contracts_match_and_do_not_allow_self_provisioning():
    """Административная команда согласована, вход и профиль не несут ручной флаг."""
    admin, user = AdminCommand.model_json_schema(), UserCommand.model_json_schema()
    for field in ("properties", "required", "additionalProperties"):
        assert admin[field] == user[field]
    assert "review_access_enabled" not in ProvisionRequest.model_fields
    assert "review_access_enabled" not in UserResponse.model_fields


def test_review_management_has_no_database_or_http_dependencies():
    """Домен и сценарий назначения зависят только от собственных портов."""
    for relative in (
        "domain/review_access.py",
        "application/use_cases/review_access.py",
    ):
        source = ROOT / "services/user-service/src/pdrd_user_service" / relative
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    (
                        "sqlalchemy",
                        "httpx",
                        "fastapi",
                        "pdrd_auth_service",
                        "pdrd_admin_service",
                    )
                )
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith(("sqlalchemy", "httpx", "fastapi"))
                    for alias in node.names
                )
