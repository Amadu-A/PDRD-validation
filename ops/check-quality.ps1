# ops/check-quality.ps1

<#
Проверка качества monorepo под Windows без большого блока вставки в PSReadLine.

Скрипт выбирает .venv-dev или .venv, находит Node для JS-тестов, проверяет
зависимости, Ruff, весь pytest и пробелы Git. Каждый внешний процесс проверяется
по коду завершения; pytest получает новый временный каталог и не создаёт кеш.

-Fix сначала применяет исправления Ruff. Скрипт не индексирует, не коммитит
и не отправляет файлы: публикация выполняется отдельно после просмотра diff.
#>

[CmdletBinding()]
param(
    [switch]$Fix
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Invoke-CheckedCommand {
    <# Выполняет внешний процесс и останавливает проверку при ненулевом коде. #>
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Код завершения: $LASTEXITCODE."
    }
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$safeDirectory = $repositoryRoot.Replace("\", "/")
$gitArguments = @("--no-pager", "-c", "safe.directory=$safeDirectory")

$pythonExecutable = $null
foreach ($candidate in @(".venv-dev\Scripts\python.exe", ".venv\Scripts\python.exe")) {
    $candidatePath = Join-Path $repositoryRoot $candidate
    if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
        $pythonExecutable = $candidatePath
        break
    }
}
if (-not $pythonExecutable) {
    throw "В корне проекта не найден Python из .venv-dev или .venv."
}

$previousPath = $env:PATH
$previousPythonIoEncoding = $env:PYTHONIOENCODING
$previousPythonPath = $env:PYTHONPATH
Push-Location $repositoryRoot
try {
    # Совпадает с UTF-8 OutputEncoding PowerShell и сохраняет русский вывод pytest.
    $env:PYTHONIOENCODING = "utf-8"
    # Новые сервисы ещё могут отсутствовать в существующем локальном venv.
    $userServiceSource = Join-Path $repositoryRoot "services\user-service\src"
    $authServiceSource = Join-Path $repositoryRoot "services\auth-service\src"
    $adminServiceSource = Join-Path $repositoryRoot "services\admin-service\src"
    $identitySources = @(
        $userServiceSource, $authServiceSource, $adminServiceSource
    ) -join [IO.Path]::PathSeparator
    if ([string]::IsNullOrEmpty($previousPythonPath)) {
        $env:PYTHONPATH = $identitySources
    }
    else {
        $env:PYTHONPATH = "$identitySources$([IO.Path]::PathSeparator)$previousPythonPath"
    }
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCommand) {
        $nodeDirectory = Join-Path $env:ProgramFiles "nodejs"
        $nodeExecutable = Join-Path $nodeDirectory "node.exe"
        if (-not (Test-Path -LiteralPath $nodeExecutable -PathType Leaf)) {
            throw "Node.js не найден в PATH или Program Files\nodejs. Он необходим для frontend-тестов."
        }
        $env:PATH = "$nodeDirectory;$previousPath"
        $nodeCommand = Get-Command node -ErrorAction Stop
    }
    $gitCommand = Get-Command git -ErrorAction Stop
    Invoke-CheckedCommand $pythonExecutable @("--version") "Не удалось запустить Python."
    Invoke-CheckedCommand $nodeCommand.Source @("--version") "Не удалось запустить Node.js."
    Invoke-CheckedCommand $gitCommand.Source @("--version") "Не удалось запустить Git."

    Invoke-CheckedCommand $pythonExecutable @("-m", "pip", "check") "Нарушена совместимость зависимостей."
    if ($Fix) {
        Invoke-CheckedCommand $pythonExecutable @("-m", "ruff", "check", ".", "--fix") "Ruff не смог исправить нарушения."
        Invoke-CheckedCommand $pythonExecutable @("-m", "ruff", "format", ".") "Ruff не смог отформатировать код."
    }
    Invoke-CheckedCommand $pythonExecutable @("-m", "ruff", "check", ".") "Ruff обнаружил ошибки."
    Invoke-CheckedCommand $pythonExecutable @("-m", "ruff", "format", "--check", ".") "Форматирование не соответствует Ruff."

    $pytestBase = Join-Path ([System.IO.Path]::GetTempPath()) ("pdrd-quality-" + [guid]::NewGuid().ToString("N"))
    Invoke-CheckedCommand $pythonExecutable @(
        "-m", "pytest", "-q", "--import-mode=importlib",
        "-p", "no:cacheprovider", "--basetemp", $pytestBase
    ) "Pytest завершился с ошибкой."

    # PowerShell не раскрывает маску для внешней команды; передаём Node реальные файлы.
    $jsTestFiles = @(
        Get-ChildItem -LiteralPath (Join-Path $repositoryRoot "frontend/tests") -Filter "*.test.js" -File |
            Sort-Object Name |
            ForEach-Object { $_.FullName }
    )
    if ($jsTestFiles.Count -eq 0) {
        throw "Frontend-тесты *.test.js не найдены."
    }
    Invoke-CheckedCommand $nodeCommand.Source (@("--test") + $jsTestFiles) "Frontend-тесты завершились с ошибкой."
    Invoke-CheckedCommand $gitCommand.Source ($gitArguments + @("diff", "--check")) "Git обнаружил ошибки пробелов."

    Write-Host "Все проверки качества успешно завершены."
}
finally {
    $env:PATH = $previousPath
    $env:PYTHONIOENCODING = $previousPythonIoEncoding
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
