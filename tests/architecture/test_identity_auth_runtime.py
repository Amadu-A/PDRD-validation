# tests/architecture/test_identity_auth_runtime.py

"""Защищает opt-in запуск auth/admin и передачу секретов между сервисами."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
UP_SCRIPT = ROOT / "scripts" / "up.sh"
CHECK_SCRIPT = ROOT / "scripts" / "check-stack.sh"


def test_auth_profile_stays_private_and_waits_for_identity() -> None:
    """Auth/admin не открывают host-порты и зависят от миграции и user-service."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    migrator = services["auth-migrate"]
    auth = services["auth-service"]
    admin = services["admin-service"]

    for service in (migrator, auth, admin):
        assert service["profiles"] == ["auth"]
        assert service["networks"] == ["app-net"]
        assert "ports" not in service

    assert "env_file" not in migrator
    assert migrator["command"] == [
        "python",
        "-m",
        "alembic",
        "-c",
        "alembic.ini",
        "upgrade",
        "head",
    ]
    assert migrator["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert auth["depends_on"]["auth-migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert auth["depends_on"]["user-service"]["condition"] == "service_healthy"
    assert admin["depends_on"]["auth-service"]["condition"] == "service_healthy"
    assert admin["depends_on"]["user-service"]["condition"] == "service_healthy"
    assert "/health/ready" in " ".join(auth["healthcheck"]["test"])
    assert "/health/ready" in " ".join(admin["healthcheck"]["test"])


def test_auth_configuration_keeps_ad_disabled_and_uses_existing_project_secrets() -> (
    None
):
    """HTTP/email работают отдельно от AD, а ключи и пароль БД берутся из .env."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    auth_service = services["auth-service"]
    auth = auth_service["environment"]
    admin = services["admin-service"]["environment"]
    gateway = services["api-gateway"]["environment"]

    assert auth["AUTH_SERVICE_ENABLED"] == "${AUTH_SERVICE_ENABLED:-false}"
    assert auth["AUTH_SERVICE_AD__CA_BUNDLE_PATH"] == (
        "/run/pdrd-auth-certificates/ad-ca.pem"
    )
    assert auth["AUTH_SERVICE_HTTP__ENABLED"] == "true"
    assert auth["AUTH_SERVICE_EMAIL__ENABLED"] == "true"
    assert auth["AUTH_SERVICE_HTTP__COOKIE_SECURE"] == "true"
    assert auth["AUTH_SERVICE_DATABASE__PASSWORD"] == (
        "${PDRD_POSTGRES_PASSWORD:?PDRD_POSTGRES_PASSWORD must be set in .env}"
    )
    assert auth["AUTH_SERVICE_HTTP__USER_SERVICE_INTERNAL_KEY"] == (
        "${USER_SERVICE_INTERNAL_KEY:-}"
    )
    assert admin["ADMIN_SERVICE_AUTH_SERVICE_INTERNAL_KEY"] == (
        "${AUTH_SERVICE_HTTP__INTERNAL_KEY:-}"
    )
    assert admin["ADMIN_SERVICE_USER_SERVICE_INTERNAL_KEY"] == (
        "${USER_SERVICE_INTERNAL_KEY:-}"
    )
    assert gateway["API_GATEWAY_IDENTITY_PROXY__ENABLED"] == (
        "${API_GATEWAY_IDENTITY_PROXY__ENABLED:-false}"
    )
    assert gateway["API_GATEWAY_IDENTITY_PROXY__AUTHORIZATION_ENABLED"] == (
        "${API_GATEWAY_IDENTITY_PROXY__AUTHORIZATION_ENABLED:-false}"
    )
    assert gateway["API_GATEWAY_IDENTITY_PROXY__AUTH_INTERNAL_KEY"] == (
        "${AUTH_SERVICE_HTTP__INTERNAL_KEY:-}"
    )
    assert gateway["API_GATEWAY_IDENTITY_PROXY__USER_SERVICE_INTERNAL_KEY"] == (
        "${USER_SERVICE_INTERNAL_KEY:-}"
    )
    assert gateway["API_GATEWAY_IDENTITY_PROXY__TRUSTED_PROXY_KEY"] == (
        "${PDRD_FRONTEND_PROXY_KEY:-}"
    )
    assert gateway["API_GATEWAY_IDENTITY_PROXY__TECHNICAL_ASSIGNMENT_ACCESS_KEY"] == (
        "${PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY:-}"
    )
    assert services["frontend"]["environment"]["PDRD_FRONTEND_PROXY_KEY"] == (
        "${PDRD_FRONTEND_PROXY_KEY:-}"
    )
    assert services["review-frontend"]["environment"]["PDRD_FRONTEND_PROXY_KEY"] == (
        "${PDRD_FRONTEND_PROXY_KEY:-}"
    )
    assert auth_service["volumes"] == [
        "./ops/certificates:/run/pdrd-auth-certificates:ro"
    ]


def test_private_datastores_bind_only_to_server_loopback_by_default() -> None:
    """HTTP без TLS и хранилища по умолчанию доступны только через loopback."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert services["frontend"]["ports"] == [
        "${FRONTEND_BIND_IP:-127.0.0.1}:${FRONTEND_PORT:-8080}:80"
    ]
    assert services["postgres"]["ports"] == [
        "${PDRD_POSTGRES_BIND_IP:-127.0.0.1}:${PDRD_POSTGRES_HOST_PORT:-5432}:5432"
    ]
    assert services["qdrant"]["ports"] == [
        "${QDRANT_BIND_IP:-127.0.0.1}:${QDRANT_HTTP_PORT:-6333}:6333",
        "${QDRANT_BIND_IP:-127.0.0.1}:${QDRANT_GRPC_PORT:-6334}:6334",
    ]


def test_example_describes_https_email_and_secret_overrides() -> None:
    """Публичный шаблон не содержит реальных SMTP и служебных секретов."""
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value

    assert values["COMPOSE_PROFILES"] == ""
    assert values["AUTH_SERVICE_ENABLED"] == "false"
    assert values["AUTH_SERVICE_AD__CA_BUNDLE_PATH"] == (
        "/run/pdrd-auth-certificates/ad-ca.pem"
    )
    assert values["AUTH_SERVICE_HTTP__PUBLIC_ORIGIN"] == ""
    assert values["AUTH_SERVICE_HTTP__INTERNAL_KEY"] == ""
    assert values["AUTH_SERVICE_HTTP__CSRF_KEY"] == ""
    assert values["PDRD_FRONTEND_PROXY_KEY"] == ""
    assert values["PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY"] == ""
    assert values["AUTH_SERVICE_EMAIL__SMTP_HOST"] == "smtp.yandex.ru"
    assert values["AUTH_SERVICE_EMAIL__SMTP_PORT"] == "465"
    assert values["AUTH_SERVICE_EMAIL__USE_SSL"] == "true"
    assert values["AUTH_SERVICE_EMAIL__USE_STARTTLS"] == "false"
    assert values["AUTH_SERVICE_EMAIL__SMTP_PASSWORD"] == ""
    assert values["AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL"] == ""
    assert values["ADMIN_SERVICE_AUTH_SERVICE_INTERNAL_KEY"] == ""
    assert values["KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED"] == "false"


def test_startup_validates_auth_profile_and_checks_internal_health() -> None:
    """Единые скрипты требуют identity, секреты и готовность обоих сервисов."""
    startup = UP_SCRIPT.read_text(encoding="utf-8")
    check = CHECK_SCRIPT.read_text(encoding="utf-8")

    assert 'if profile_enabled "auth"; then' in startup
    assert 'profile_enabled "identity"' in startup
    assert 'validate_secret "AUTH_SERVICE_HTTP__INTERNAL_KEY"' in startup
    assert 'validate_secret "AUTH_SERVICE_HTTP__CSRF_KEY"' in startup
    assert 'validate_secret "PDRD_FRONTEND_PROXY_KEY"' in startup
    assert 'validate_secret "PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY"' in startup
    assert 'validate_secret "AUTH_SERVICE_EMAIL__SMTP_PASSWORD"' in startup
    assert '"${AUTH_SERVICE_HTTP__PUBLIC_ORIGIN}" != https://*' in startup
    assert "ops/certificates/ad-ca.pem" in startup
    assert "AUTH_SERVICE_ENABLED:-false" in startup
    assert "API_GATEWAY_IDENTITY_PROXY__ENABLED=true" in startup
    assert "API_GATEWAY_IDENTITY_PROXY__AUTHORIZATION_ENABLED=true" in startup
    assert "log_services+=(auth-migrate auth-service admin-service)" in startup

    assert 'optional_profile_active "auth"' in check
    assert 'check_service_state "auth-service" "auth"' in check
    assert 'check_service_state "admin-service" "auth"' in check
    assert 'check_migrations_current "auth-service" "auth" "Auth Service"' in check
    assert 'bad "auth requires API Gateway identity proxy"' in check
    assert 'bad "auth requires API Gateway authorization"' in check
    assert 'bad "auth requires AUTH_SERVICE_HTTP__PUBLIC_ORIGIN with HTTPS"' in check
    assert 'bad "auth requires matching AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL"' in check
    assert "corporate AD requires readable ops/certificates/ad-ca.pem" in check
    assert (
        'bad "auth requires PDRD_FRONTEND_PROXY_KEY (at least 32 characters)"' in check
    )
    assert (
        'bad "auth requires PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY '
        '(at least 32 characters)"' in check
    )
    assert 'ok "Auth Service ready (internal)"' in check
    assert 'ok "Admin Service ready (internal)"' in check
    assert "/api/v1/auth/session" in check
    assert "/api/v1/admin/users" in check
    assert '"Review frontend -> API Gateway proxy requires login"' in check


def test_admin_package_participates_in_complete_quality() -> None:
    """Единая проверка запускает тесты admin-service и ставит его зависимости."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert '"services/admin-service/tests"' in pyproject
    assert "-e ./services/admin-service[test]" in requirements
