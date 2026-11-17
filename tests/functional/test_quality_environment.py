# tests/functional/test_quality_environment.py

"""Общие тесты изолированы от рабочих интеграций, а production по-прежнему требует ключи."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FLAGS = (
    "DOCUMENT_SERVICE_SPECIFICATION_SERVICE__ENABLED",
    "DOCUMENT_SERVICE_DOCLING__ENABLED",
)
SECRETS = (
    "SPEC_EXTRACTING_AI_API_KEY",
    "DOCLING_API_KEY",
    "DOCUMENT_SERVICE_SPECIFICATION_SERVICE__API_KEY",
    "DOCUMENT_SERVICE_DOCLING__API_KEY",
)


def quality_environment() -> dict[str, str]:
    """Читает именно окружение опубликованного quality image/Compose, без рабочих секретов."""
    result = {}
    for line in (
        (ROOT / "ops/Dockerfile.quality").read_text(encoding="utf-8").splitlines()
    ):
        if line.startswith("ENV "):
            result.update(part.split("=", 1) for part in shlex.split(line[4:]))
    service = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))[
        "services"
    ]["quality-tests"]
    assert not service.get("env_file"), "Quality не должен загружать промышленный .env"
    result.update({k: str(v) for k, v in service.get("environment", {}).items()})
    return result


@pytest.mark.parametrize("isolated", [False, True])
def test_actual_quality_settings_collect_http_modules_without_production_keys(
    tmp_path, isolated
):
    """True defaults без ключей ломают обычный запуск, но не импорт модулей в quality-контейнере."""
    (tmp_path / ".env.example").write_text(
        "\n".join(f"{name}=true" for name in FLAGS)
        + "\n"
        + "\n".join(f"{name}=" for name in SECRETS)
        + "\n",
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items() if key not in (*FLAGS, *SECRETS)
    }
    environment["PYTHONPATH"] = (
        str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    if isolated:
        environment.update(quality_environment())
    files = [
        "tests/functional/test_artifact_dataset_api.py",
        "tests/functional/test_reviewed_pdf_api.py",
        "services/document-service/tests/integration/test_pdf_selection_http.py",
        "services/analysis-service/tests/integration/test_quality_stage3_http.py",
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *(str(ROOT / p) for p in files),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    if isolated:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "collected" in result.stdout
    else:
        assert result.returncode == 2
        assert (
            "ValidationError" in result.stdout
            and "specification_service" in result.stdout
        )
        assert "docling" in result.stdout


def test_production_import_accepts_public_keys_and_keeps_integrations_enabled(tmp_path):
    """Рабочие флаги и публичные ключи поддерживаются без ослабления production-валидации."""
    (tmp_path / ".env.example").write_text(
        "\n".join(f"{name}=true" for name in FLAGS)
        + "\nSPEC_EXTRACTING_AI_API_KEY=\nDOCLING_API_KEY=\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "SPEC_EXTRACTING_AI_API_KEY=spec-isolated-test\nDOCLING_API_KEY=docling-isolated-test\n",
        encoding="utf-8",
    )
    environment = {k: v for k, v in os.environ.items() if k not in (*FLAGS, *SECRETS)}
    code = """from unittest.mock import patch
with patch('socket.socket.connect', side_effect=AssertionError('Unexpected network')):
    from pdrd_document_service.main import app
    from pdrd_document_service.core.settings import Settings
    s = Settings()
    assert s.specification_service.enabled and s.docling.enabled
    assert s.specification_service.api_key.get_secret_value() == 'spec-isolated-test'
    assert s.docling.api_key.get_secret_value() == 'docling-isolated-test'
print('PRODUCTION_CONFIG_VALID')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRODUCTION_CONFIG_VALID" in result.stdout


@pytest.mark.skipif(
    sys.platform != "win32", reason="Проверка PowerShell-окружения Windows"
)
@pytest.mark.parametrize("fail", [False, True])
def test_windows_gate_restores_caller_flags_even_after_failure(tmp_path, fail):
    """Настоящий try/finally скрипта восстанавливает заданный флаг и отсутствие другого."""
    executable = shutil.which("powershell")
    assert executable
    source = (ROOT / "ops/check-quality.ps1").read_text(encoding="utf-8-sig")
    boundary = "& $Executable @Arguments"
    assert source.count(boundary) == 1
    # Подменяется только граница внешних команд: Git, Python и Node не запускаются.
    source = source.replace(
        boundary,
        """
    foreach ($flag in $integrationFlags) {
        if ([Environment]::GetEnvironmentVariable($flag, 'Process') -ne 'false') {
            throw 'QUALITY_FLAGS_NOT_ISOLATED'
        }
    }
    $global:LASTEXITCODE = if ($env:PDRD_QUALITY_FIXTURE_FAIL -eq '1') { 17 } else { 0 }
""",
    )
    script = tmp_path / "ops/check-quality.ps1"
    script.parent.mkdir()
    script.write_text(source, encoding="utf-8-sig")
    interpreter = tmp_path / ".venv/Scripts/python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    js = tmp_path / "frontend/tests/stub.test.js"
    js.parent.mkdir(parents=True)
    js.touch()
    environment = {**os.environ, "PDRD_QUALITY_FIXTURE_FAIL": "1" if fail else "0"}
    command = f"""
$env:{FLAGS[0]} = 'true'
[Environment]::SetEnvironmentVariable('{FLAGS[1]}', $null, 'Process')
$failed = $false
try {{ & '{script.as_posix()}' }} catch {{ $failed = $true }}
if ($failed -ne ($env:PDRD_QUALITY_FIXTURE_FAIL -eq '1')) {{ throw 'WRONG_GATE_RESULT' }}
if ($env:{FLAGS[0]} -ne 'true') {{ throw 'CALLER_FLAG_LOST' }}
if ($null -ne [Environment]::GetEnvironmentVariable('{FLAGS[1]}', 'Process')) {{ throw 'CALLER_FLAG_LEAKED' }}
Write-Output 'CALLER_FLAGS_RESTORED'
"""
    result = subprocess.run(
        [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        env=environment,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CALLER_FLAGS_RESTORED" in result.stdout
