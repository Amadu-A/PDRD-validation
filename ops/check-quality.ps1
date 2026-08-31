# ops/check-quality.ps1

<#
Локальная проверка качества Python-кода PDRD под Windows.

Скрипт ищет Python сначала в .venv-dev, затем в обычной .venv.
При параметре -Fix Ruff сначала исправляет безопасно исправляемые нарушения
и форматирует код. После этого выполняются lint, format check и pytest.

Для monorepo pytest запускается в importlib mode, чтобы тестовые файлы
с одинаковыми именами в разных микросервисах не конфликтовали между собой.

Файл относится к development tooling и не участвует в runtime приложения.
#>

[CmdletBinding()]
param(
    [switch]$Fix
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repositoryRoot = (
    Resolve-Path (
        Join-Path $PSScriptRoot ".."
    )
).Path

$devPython = Join-Path `
    $repositoryRoot `
    ".venv-dev\Scripts\python.exe"

$projectPython = Join-Path `
    $repositoryRoot `
    ".venv\Scripts\python.exe"

if (Test-Path $devPython) {
    $pythonExecutable = $devPython
}
elseif (Test-Path $projectPython) {
    $pythonExecutable = $projectPython
}
else {
    throw (
        "Python environment was not found. " +
        "Expected .venv-dev or .venv in repository root."
    )
}

Write-Host "Python executable:"
Write-Host $pythonExecutable

& $pythonExecutable --version

if ($LASTEXITCODE -ne 0) {
    throw "Python executable check failed."
}

Push-Location $repositoryRoot

try {
    if ($Fix) {
        Write-Host "Ruff: automatic fixes..."

        & $pythonExecutable -m ruff check . --fix

        if ($LASTEXITCODE -ne 0) {
            throw "ruff check --fix failed."
        }

        Write-Host "Ruff: formatting..."

        & $pythonExecutable -m ruff format .

        if ($LASTEXITCODE -ne 0) {
            throw "ruff format failed."
        }
    }

    Write-Host "Ruff: lint check..."

    & $pythonExecutable -m ruff check .

    if ($LASTEXITCODE -ne 0) {
        throw "ruff check found errors."
    }

    Write-Host "Ruff: format check..."

    & $pythonExecutable -m ruff format --check .

    if ($LASTEXITCODE -ne 0) {
        throw "ruff format --check found errors."
    }

    Write-Host "Pytest..."

    & $pythonExecutable `
        -m pytest `
        -q `
        --import-mode=importlib

    if ($LASTEXITCODE -ne 0) {
        throw "pytest failed."
    }

    Write-Host "All quality checks passed."
}
finally {
    Pop-Location
}