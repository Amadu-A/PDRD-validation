# tests/functional/test_linux_quality_gate.py

"""Проверяет порядок Linux-проверок, остановку при ошибке и очистку контейнеров."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

QUALITY_SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "check-quality.sh"
SUITES = ("gateway", "knowledge", "experience", "user", "auth", "equipment")


@pytest.fixture
def run_quality_gate(tmp_path: Path):
    """Запускает настоящий Bash-скрипт с подменой Git и Docker без контейнеров."""
    git_bash = Path("C:/Program Files/Git/usr/bin/bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if bash is None:
        pytest.skip("Для проверки Linux-скрипта нужен Bash или Git Bash")
    log = tmp_path / "commands.log"
    stubs = """git() {
    printf '%s\\n' "git $*" >> "${QUALITY_TEST_LOG}"
    if [[ "${QUALITY_FAIL_OPERATION}" == "git" ]]; then
        return "${QUALITY_FAIL_EXIT}"
    fi
    return 0
}
docker() {
    printf '%s\\n' "docker $*" >> "${QUALITY_TEST_LOG}"
    local suite=""
    local operation="quality"
    local argument
    for argument in "$@"; do
        case "${argument}" in
            pdrd-*-test)
                suite="${argument#pdrd-}"
                suite="${suite%-test}"
                ;;
            up|down) operation="${suite}:${argument}" ;;
        esac
    done
    if [[ "${operation}" == "${QUALITY_FAIL_OPERATION}" ]]; then
        return "${QUALITY_FAIL_EXIT}"
    fi
    if [[ "${operation}" == "${QUALITY_FAIL_CLEANUP}" ]]; then
        return 29
    fi
    return 0
}
source "$1"
"""

    def run(failure="", *, exit_code=19, cleanup_failure=""):
        """Задаёт сбой внешней команды и возвращает вывод с журналом вызовов."""
        environment = {
            **os.environ,
            "QUALITY_TEST_LOG": log.as_posix(),
            "QUALITY_FAIL_OPERATION": failure,
            "QUALITY_FAIL_EXIT": str(exit_code),
            "QUALITY_FAIL_CLEANUP": cleanup_failure,
        }
        result = subprocess.run(
            [
                bash,
                "--noprofile",
                "--norc",
                "-c",
                stubs,
                "quality-gate-test",
                QUALITY_SCRIPT.as_posix(),
            ],
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        if not log.is_file():
            pytest.fail(f"Bash не вызвал подменённые команды: {result.stderr}")
        commands = log.read_text(encoding="utf-8").splitlines()
        return result, commands

    return run


def _operations(commands):
    """Преобразует журнал внешних команд в последовательность этапов."""
    operations = []
    for command in commands:
        if command.startswith("git "):
            operations.append("git")
        elif "quality-tests" in command:
            operations.append("quality")
        else:
            suite = next(suite for suite in SUITES if f"pdrd-{suite}-test" in command)
            operation = "up" if " up " in command else "down"
            operations.append(f"{suite}:{operation}")
    return operations


def test_linux_quality_gate_runs_all_suites_and_cleans_each(run_quality_gate):
    """Успешный запуск проверяет семь этапов и очищает каждый тестовый проект."""
    result, commands = run_quality_gate()
    assert result.returncode == 0, result.stderr
    assert _operations(commands) == [
        "git",
        "quality",
        *(f"{suite}:{operation}" for suite in SUITES for operation in ("up", "down")),
    ]
    assert "[7/7]" in result.stdout
    assert "Все проверки прошли" in result.stdout
    for suite in SUITES:
        up, down = [command for command in commands if f"pdrd-{suite}-test" in command]
        assert f"--exit-code-from {suite}-test-runner" in up
        assert f"ops/compose.{suite}-test.yaml" in down
        assert down.endswith("down --remove-orphans")


@pytest.mark.parametrize("failure", ["git", "quality"])
def test_linux_quality_gate_stops_before_integration_on_initial_failure(
    run_quality_gate, failure
):
    """Ошибка общих проверок сохраняет код и не запускает интеграционные наборы."""
    result, commands = run_quality_gate(failure, exit_code=17)
    assert result.returncode == 17
    assert _operations(commands) == (
        ["git"] if failure == "git" else ["git", "quality"]
    )
    assert "Общие проверки" in result.stderr
    assert "Все проверки прошли" not in result.stdout


@pytest.mark.parametrize("suite", SUITES)
def test_linux_quality_gate_cleans_failed_runner_and_stops(run_quality_gate, suite):
    """Сбой runner очищает его проект, сохраняет код и не запускает следующий набор."""
    result, commands = run_quality_gate(f"{suite}:up", exit_code=23)
    preceding = SUITES[: SUITES.index(suite)]
    assert result.returncode == 23
    assert _operations(commands) == [
        "git",
        "quality",
        *(f"{item}:{operation}" for item in preceding for operation in ("up", "down")),
        f"{suite}:up",
        f"{suite}:down",
    ]
    assert f"Интеграционные тесты {suite}" in result.stderr
    assert "Все проверки прошли" not in result.stdout


def test_linux_quality_gate_stops_if_cleanup_fails(run_quality_gate):
    """Сбой очистки приводит к повторной попытке и запрещает продолжение проверки."""
    result, commands = run_quality_gate(cleanup_failure="gateway:down")
    assert result.returncode == 29
    assert _operations(commands) == [
        "git",
        "quality",
        "gateway:up",
        "gateway:down",
        "gateway:down",
    ]
    assert "Интеграционные тесты gateway" in result.stderr


def test_linux_quality_gate_preserves_runner_error_when_cleanup_also_fails(
    run_quality_gate,
):
    """Ошибка очистки не скрывает исходный код завершения упавшего runner."""
    result, commands = run_quality_gate(
        "gateway:up", exit_code=23, cleanup_failure="gateway:down"
    )
    assert result.returncode == 23
    assert _operations(commands) == ["git", "quality", "gateway:up", "gateway:down"]
    assert "код 23" in result.stderr
