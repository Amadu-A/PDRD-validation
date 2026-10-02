# tests/functional/test_stack_optional_profiles.py

"""Проверяет выбор необязательных контейнеров без обращения к Docker daemon."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _bash_executable() -> str:
    """Находит Bash в Linux и Git Bash в Windows для одного контракта скрипта."""
    if os.name == "nt":
        candidates = (
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Git"
            / "bin"
            / "bash.exe",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Git"
            / "usr"
            / "bin"
            / "bash.exe",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("Bash отсутствует: проверка скриптов требует Bash")
    return executable


def test_optional_profile_detects_existing_container_and_configured_profile() -> None:
    """Запущенный Review виден без флага; пустой Identity явно пропускается."""
    shell = r"""
        set -euo pipefail
        source scripts/lib/stack-profiles.sh
        docker() {
            case "$*" in
                "compose --profile review ps --all --quiet experience-service")
                    printf 'existing-review-container\n'
                    ;;
                "compose --profile identity ps --all --quiet user-service")
                    if [[ "${mock_identity_present:-0}" == 1 ]]; then
                        printf 'existing-identity-container\n'
                    fi
                    ;;
            esac
        }
        unset COMPOSE_PROFILES
        optional_profile_active review experience-service || exit 10
        if optional_profile_active identity user-service; then exit 11; fi
        mock_identity_present=1
        optional_profile_active identity user-service || exit 13
        mock_identity_present=0
        COMPOSE_PROFILES=identity
        optional_profile_active identity user-service || exit 12
        printf 'optional profiles: OK\n'
    """

    # Git Bash на Windows иногда падает внутри MSYS до исполнения скрипта.
    # Повторяем только эту ошибку запуска; сбой проверяемой функции не маскируем.
    for _ in range(3):
        result = subprocess.run(
            [_bash_executable(), "-c", shell],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
        if not (
            os.name == "nt"
            and result.returncode != 0
            and "fatal error - add_item" in result.stderr
        ):
            break

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "optional profiles: OK"


def _run_startup_preflight(
    tmp_path: Path, environment: str
) -> subprocess.CompletedProcess[str]:
    """Запускает только раннюю валидацию up.sh в изолированной копии."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts" / "up.sh", scripts / "up.sh")
    shutil.copy2(ROOT / ".env.example", tmp_path / ".env.example")
    (tmp_path / ".env").write_text(environment, encoding="utf-8")

    return subprocess.run(
        [_bash_executable(), "-c", "bash scripts/up.sh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=90,
    )


def _auth_preflight_environment(**overrides: str) -> str:
    """Возвращает безопасный набор фиктивных значений для auth preflight."""
    values = {
        "PDRD_POSTGRES_PASSWORD": "test-postgres-password",
        "PDRD_RABBITMQ_PASSWORD": "test-rabbitmq-password",
        "COMPOSE_PROFILES": "identity,auth",
        "USER_SERVICE_INTERNAL_KEY": "u" * 32,
        "AUTH_SERVICE_ENABLED": "false",
        "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN": "https://pdrd.itcneoterm.local",
        "AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL": "https://pdrd.itcneoterm.local",
        "AUTH_SERVICE_HTTP__INTERNAL_KEY": "i" * 32,
        "AUTH_SERVICE_HTTP__CSRF_KEY": "c" * 32,
        "PDRD_FRONTEND_PROXY_KEY": "f" * 32,
        "PDRD_TECHNICAL_ASSIGNMENT_ACCESS_KEY": "t" * 32,
        "AUTH_SERVICE_EMAIL__SMTP_USER": "test@example.invalid",
        "AUTH_SERVICE_EMAIL__SMTP_PASSWORD": "test-smtp-password",
        "AUTH_SERVICE_EMAIL__FROM_EMAIL": "test@example.invalid",
        "API_GATEWAY_IDENTITY_PROXY__ENABLED": "true",
        "API_GATEWAY_IDENTITY_PROXY__AUTHORIZATION_ENABLED": "true",
    }
    values.update(overrides)
    return "".join(f"{name}={value}\n" for name, value in values.items())


def test_auth_preflight_rejects_missing_browser_origin(tmp_path: Path) -> None:
    """Профиль auth сообщает имя отсутствующей настройки до обращения к Docker."""
    result = _run_startup_preflight(
        tmp_path,
        _auth_preflight_environment(AUTH_SERVICE_HTTP__PUBLIC_ORIGIN=""),
    )

    assert result.returncode == 1
    assert "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN должен быть задан" in result.stderr


def test_auth_preflight_rejects_http_browser_origin(tmp_path: Path) -> None:
    """Пароль и session cookie не разрешаются через внутренний HTTP по IP."""
    result = _run_startup_preflight(
        tmp_path,
        _auth_preflight_environment(
            AUTH_SERVICE_HTTP__PUBLIC_ORIGIN="http://192.168.55.3:8080",
            AUTH_SERVICE_EMAIL__PUBLIC_BASE_URL="http://192.168.55.3:8080",
        ),
    )

    assert result.returncode == 1
    assert (
        "AUTH_SERVICE_HTTP__PUBLIC_ORIGIN должен начинаться с https://" in result.stderr
    )


def test_corporate_auth_preflight_requires_deployed_ca(tmp_path: Path) -> None:
    """Включённый AD останавливается с понятной ошибкой до запуска контейнеров."""
    result = _run_startup_preflight(
        tmp_path,
        _auth_preflight_environment(AUTH_SERVICE_ENABLED="true"),
    )

    assert result.returncode == 1
    assert "нужен непустой читаемый ops/certificates/ad-ca.pem" in result.stderr
