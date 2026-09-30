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
