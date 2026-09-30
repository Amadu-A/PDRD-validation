# ops/check-quality.ps1

<#
Проверка качества monorepo под Windows без большого блока вставки в PSReadLine.

Скрипт выбирает .venv-dev или .venv, находит Node для JS-тестов, проверяет
зависимости, Ruff, весь pytest и пробелы Git. Каждый внешний процесс проверяется
по коду завершения; pytest получает новый временный каталог и не создаёт кеш.

-Fix сначала применяет исправления Ruff. -CommitMessage после успешных проверок
коммитит все неигнорируемые изменения на feature/experience-base. -Push требует
-CommitMessage и отправляет эту ветку по push-адресам origin через
системный OpenSSH. Отдельные remotes, например neoterm, пушатся отдельно.
Без этих параметров скрипт только проверяет рабочую копию.
#>

[CmdletBinding()]
param(
    [switch]$Fix,
    [string]$CommitMessage,
    [switch]$Push
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
$hasCommitMessage = -not [string]::IsNullOrWhiteSpace($CommitMessage)

if ($PSBoundParameters.ContainsKey("CommitMessage") -and -not $hasCommitMessage) {
    throw "Сообщение коммита не должно быть пустым."
}
if ($Push -and -not $hasCommitMessage) {
    throw "Для -Push нужно указать -CommitMessage; push выполняется после проверок и коммита."
}

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
Push-Location $repositoryRoot
try {
    # Совпадает с UTF-8 OutputEncoding PowerShell и сохраняет русский вывод pytest.
    $env:PYTHONIOENCODING = "utf-8"
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

    if ($hasCommitMessage) {
        $branch = & $gitCommand.Source @gitArguments branch --show-current
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось определить ветку Git."
        }
        if ($branch -ne "feature/experience-base") {
            throw "Коммит разрешён в feature/experience-base; текущая ветка: $branch."
        }
        Invoke-CheckedCommand $gitCommand.Source ($gitArguments + @("status", "--short")) "Не удалось прочитать состояние Git."
    }

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

    if ($hasCommitMessage) {
        Invoke-CheckedCommand $gitCommand.Source ($gitArguments + @("add", "--all")) "Не удалось подготовить изменения к коммиту."
        Invoke-CheckedCommand $gitCommand.Source ($gitArguments + @("diff", "--cached", "--check")) "В подготовленных изменениях есть ошибки пробелов."
        & $gitCommand.Source @gitArguments diff --cached --quiet
        $stagedExitCode = $LASTEXITCODE
        if ($stagedExitCode -eq 0) {
            throw "Нет изменений для коммита."
        }
        if ($stagedExitCode -ne 1) {
            throw "Не удалось проверить подготовленные изменения."
        }
        Invoke-CheckedCommand $gitCommand.Source ($gitArguments + @("commit", "-m", $CommitMessage)) "Не удалось создать коммит."
        if ($Push) {
            $sshExecutable = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
            if (-not (Test-Path -LiteralPath $sshExecutable -PathType Leaf)) {
                throw "Системный OpenSSH не найден; коммит создан, push не выполнен."
            }
            $sshCommand = $sshExecutable.Replace("\", "/")
            Invoke-CheckedCommand $gitCommand.Source ($gitArguments + @(
                "-c", "core.sshCommand=$sshCommand", "push",
                "origin", "feature/experience-base"
            )) "Push не завершён; коммит сохранён локально."
        }
    }
    Write-Host "Все проверки качества успешно завершены."
}
finally {
    $env:PATH = $previousPath
    $env:PYTHONIOENCODING = $previousPythonIoEncoding
    Pop-Location
}
