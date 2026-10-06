# tests/functional/test_stack_optional_profiles.py

"""Проверяет профили и адрес frontend без обращения к Docker daemon."""

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
    shutil.copytree(ROOT / "scripts" / "lib", scripts / "lib")
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
        "PDRD_RETENTION_INTERNAL_KEY": "r" * 32,
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


@pytest.mark.parametrize("contaminated_namespace", [False, True])
def test_startup_separates_shared_compose_and_preserves_project_configuration(
    tmp_path: Path, contaminated_namespace: bool
) -> None:
    """Настоящий up.sh отделяет shared Compose и блокирует ошибочные дубликаты."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts" / "up.sh", scripts / "up.sh")
    shutil.copytree(ROOT / "scripts" / "lib", scripts / "lib")
    shutil.copy2(ROOT / ".env.example", tmp_path / ".env.example")
    shared = tmp_path / "shared-fixture"
    (shared / "scripts").mkdir(parents=True)
    (shared / ".shared-fixture").touch()
    (shared / "compose.yaml").write_text("name: shared-fixture\n", encoding="utf-8")
    (shared / ".env").write_text(
        "COMPOSE_PROJECT_NAME=shared-fixture\nCOMPOSE_PROFILES=ai-vlm\n",
        encoding="utf-8",
    )
    for name in ("bootstrap", "check"):
        (shared / "scripts" / f"{name}.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'for variable in "${!COMPOSE_@}"; do exit 42; done\n'
            '[[ "${DOCKER_CONTEXT}" == fixture-context ]]\n'
            "source .env\n"
            '[[ "${COMPOSE_PROJECT_NAME}" == shared-fixture ]]\n'
            '[[ "${COMPOSE_PROFILES}" == ai-vlm ]]\n'
            f"printf '{name}\\n' >> ../trace.log\n",
            encoding="utf-8",
        )
    (tmp_path / ".env").write_text(
        _auth_preflight_environment(
            SHARED_INFRA_DIR="shared-fixture",
            COMPOSE_FILE="pdrd-compose.yaml",
            COMPOSE_ENV_FILES=".env",
            COMPOSE_PATH_SEPARATOR=":",
            COMPOSE_DISABLE_ENV_FILE="1",
            COMPOSE_IGNORE_ORPHANS="true",
            DOCKER_CONTEXT="fixture-context",
            MOCK_CONTAMINATED_NAMESPACE="1" if contaminated_namespace else "0",
        ),
        encoding="utf-8",
    )
    shell = r"""
        docker() {
            if [[ -f .shared-fixture ]]; then
                for variable in "${!COMPOSE_@}"; do
                    printf 'shared inherited %s\n' "$variable" >&2
                    return 42
                done
                [[ "${DOCKER_CONTEXT}" == fixture-context ]] || return 43
                case "$*" in
                    "compose up "*) printf 'shared-up\n' >> ../trace.log ;;
                    "compose exec -T rabbitmq rabbitmqctl "*)
                        printf 'shared-rabbitmq\n' >> ../trace.log
                        case "${6:-}" in
                            list_vhosts) printf 'pdrd-validation\n' ;;
                            list_users) printf 'pdrd_validation\n' ;;
                        esac
                        ;;
                    *) return 44 ;;
                esac
                return 0
            fi
            [[ "${COMPOSE_PROJECT_NAME}" == pdrd-validation-ai ]] || return 45
            [[ "${COMPOSE_PROFILES}" == identity,auth ]] || return 46
            [[ "${COMPOSE_FILE}" == pdrd-compose.yaml ]] || return 47
            [[ "${COMPOSE_ENV_FILES}" == .env ]] || return 48
            [[ "${COMPOSE_PATH_SEPARATOR}" == : ]] || return 49
            [[ "${COMPOSE_DISABLE_ENV_FILE}" == 1 ]] || return 50
            [[ "${COMPOSE_IGNORE_ORPHANS}" == true ]] || return 51
            [[ "${DOCKER_CONTEXT}" == fixture-context ]] || return 52
            if [[ "${1:-}" == ps ]]; then
                if [[ "$*" == *"service=rabbitmq"* && "$MOCK_CONTAMINATED_NAMESPACE" == 1 ]]; then
                    printf 'wrong-shared-container\n'
                fi
                return 0
            fi
            [[ "$*" == 'compose config --quiet' ]] || return 53
            printf 'pdrd-compose\n' >> trace.log
            # Останавливаемся до build/migrations/up: реальный Docker не вызывается.
            return 73
        }
        export -f docker
        bash scripts/up.sh
    """
    result = subprocess.run(
        [_bash_executable(), "-c", shell],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=90,
    )

    if contaminated_namespace:
        assert result.returncode == 1, result.stderr
        assert "shared-сервис rabbitmq" in result.stderr
        assert not (tmp_path / "trace.log").exists()
    else:
        assert result.returncode == 73, result.stderr
        trace = (tmp_path / "trace.log").read_text(encoding="utf-8").splitlines()
        assert trace[:3] == ["bootstrap", "shared-up", "check"]
        assert trace.count("shared-rabbitmq") >= 4
        assert trace[-1] == "pdrd-compose"


@pytest.mark.parametrize(
    ("bind_ip", "probe_host"),
    [
        ("192.168.55.3", "192.168.55.3"),
        ("127.0.0.1", "127.0.0.1"),
        ("0.0.0.0", "127.0.0.1"),
        ("::", "[::1]"),
        ("[::]", "[::1]"),
        ("::1", "[::1]"),
        ("[2001:db8::1]", "[2001:db8::1]"),
        ("2001:db8::1", "[2001:db8::1]"),
    ],
)
def test_stack_check_uses_configured_frontend_bind(
    tmp_path: Path, bind_ip: str, probe_host: str
) -> None:
    """Все четыре маршрута frontend проверяются на выбранном адресе и порту."""
    result, urls = _run_stack_check(tmp_path, bind_ip, probe_host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STACK CHECK PASSED" in result.stdout
    base = f"http://{probe_host}:9080"
    assert {url for url in urls if ":9080/" in url} == {
        f"{base}/",
        f"{base}/api/v1/normative/sections",
        f"{base}/api/v1/auth/session",
        f"{base}/api/v1/admin/users",
    }


def test_stack_check_keeps_real_frontend_failure(tmp_path: Path) -> None:
    """Готовый контейнер не скрывает реальную ошибку соединения с frontend."""
    result, _ = _run_stack_check(
        tmp_path, "192.168.55.3", "192.168.55.3", available=False
    )
    assert result.returncode == 1
    assert "STACK CHECK FAILED" in result.stdout
    assert "Frontend HTTP: HTTP 000 (http://192.168.55.3:9080/)" in result.stdout


def _run_stack_check(
    tmp_path: Path, bind_ip: str, probe_host: str, *, available: bool = True
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Исполняет check-stack.sh с изолированными подменами Docker и curl."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts" / "check-stack.sh", scripts / "check-stack.sh")
    shutil.copytree(ROOT / "scripts" / "lib", scripts / "lib")
    shutil.copy2(ROOT / ".env.example", tmp_path / ".env.example")
    (tmp_path / ".env").write_text(
        _auth_preflight_environment(
            FRONTEND_BIND_IP=bind_ip,
            FRONTEND_PORT="9080",
            MOCK_FRONTEND_URL=f"http://{probe_host}:9080",
            MOCK_FRONTEND_AVAILABLE="1" if available else "0",
        ),
        encoding="utf-8",
    )
    shell = r"""
        docker() {
            case "$*" in
                *".State.ExitCode"*) printf '0\n' ;;
                *".State.Health"*) printf 'healthy\n' ;;
                *".State.Status"*) printf 'exited\n' ;;
                "compose --profile review ps --all --quiet "*) ;;
                "compose --profile experience-index ps --all --quiet "*) ;;
                *"ps --all --quiet "*) printf 'fixture-container\n' ;;
                *) return 0 ;;
            esac
        }
        curl() {
            local url="${@: -1}"
            printf '%s\n' "$url" >> trace.log
            if [[ "$url" == *":9080/"* ]]; then
                if [[ "$MOCK_FRONTEND_AVAILABLE" != 1
                    || "$url" != "$MOCK_FRONTEND_URL/"* ]]; then
                    printf '000'
                    return 7
                fi
            fi
            case "$url" in
                */api/v1/admin/users) printf '401' ;;
                *) printf '200' ;;
            esac
        }
        export -f docker curl
        bash scripts/check-stack.sh
    """
    result = subprocess.run(
        [_bash_executable(), "-c", shell],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=90,
    )
    trace = (tmp_path / "trace.log").read_text(encoding="utf-8").splitlines()
    return result, trace


def test_startup_rejects_missing_retention_key(tmp_path: Path) -> None:
    """Без закрытого ключа очистки стек не запускается с частично действующей политикой."""
    result = _run_startup_preflight(
        tmp_path, _auth_preflight_environment(PDRD_RETENTION_INTERNAL_KEY="")
    )
    assert result.returncode == 1
    assert "PDRD_RETENTION_INTERNAL_KEY должен быть задан" in result.stderr
