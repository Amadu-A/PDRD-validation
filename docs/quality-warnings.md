<!-- docs/quality-warnings.md -->

# Предупреждения при проверке качества

## NumPy и рендеринг DXF

В Windows-прогоне коммита `7dd69ce` все 872 теста прошли, 15 пропущены
из-за отсутствия отдельно включаемых внешних окружений. Возникли 70 одинаковых
предупреждений из `ezdxf.math._matrix44`: библиотека меняет размер матрицы
присваиванием `.shape`. В установленном NumPy 2.5.2 это устаревший API.
Изменение описано в [примечаниях к NumPy 2.5](https://numpy.org/devdocs/release/2.5.0-notes.html#deprecations).

У `ezdxf==1.4.4` нет верхней границы зависимости NumPy, поэтому разные установки
могут получать разные версии. Document Service теперь явно фиксирует
`numpy==2.4.6` — совместимую версию для Windows и Linux.
После изменения `pyproject.toml` надо обновить зависимости локальной `.venv`
и пересобрать Docker-образы проверок и Document Service.

Интеграционный тест полного DXF pipeline превращает именно это предупреждение
в ошибку. Он проверяет извлечение текста, геометрию и PNG, поэтому возврат
несовместимой зависимости не сможет незаметно пройти quality gate.
Остальные предупреждения не подавляются.

Файлы:

- `services/document-service/pyproject.toml` — совместимые runtime-зависимости;
- `services/document-service/tests/integration/test_ezdxf_processor.py` — регрессия;
- `ops/Dockerfile.quality` — устанавливает те же зависимости из исходников сервисов;
- `services/document-service/Dockerfile` — устанавливает зависимости самого сервиса.

Снять ограничение NumPy можно после перехода на стабильную версию ezdxf,
исправляющую этот API, и успешного прогона DXF-регрессии и общего набора тестов.

## Уведомление Git об имени и email

Сообщение об автоматическом выборе имени и email не означает ошибку коммита:
в предоставленном логе коммит и push завершились успешно.
Для следующих коммитов можно явно сохранить текущую эффективную подпись
только в конфигурации этого репозитория. Глобальная конфигурация не меняется.

PowerShell, из корня репозитория:

```powershell
$reviewAuthorIdent = git var GIT_AUTHOR_IDENT
if ($LASTEXITCODE -ne 0) { throw 'Не удалось прочитать подпись Git' }
$reviewAuthorMatch = [regex]::Match(
    $reviewAuthorIdent,
    '^(.+) <([^<>]+)> \d+ [+-]\d{4}$'
)
if (-not $reviewAuthorMatch.Success) { throw 'Неизвестный формат подписи Git' }
git config --local user.name ($reviewAuthorMatch.Groups[1].Value)
if ($LASTEXITCODE -ne 0) { throw 'Не удалось сохранить имя Git' }
git config --local user.email ($reviewAuthorMatch.Groups[2].Value)
if ($LASTEXITCODE -ne 0) { throw 'Не удалось сохранить email Git' }
```

Этот способ сохраняет уже используемое имя и email. Если нужна другая подпись,
укажите желаемые значения в этих двух параметрах. Переписывать опубликованный
коммит для устранения уведомления не требуется.

## Остановка изолированного PostgreSQL после тестов

`Aborting on container exit` — ожидаемое сообщение Docker Compose при запуске
с `--abort-on-container-exit`. После завершения runner остальные контейнеры
изолированного проекта останавливаются. Успех определяется кодом runner
через `--exit-code-from experience-test-runner` и результатом pytest.

В предоставленном Linux-логе: `11 passed`, runner завершился с кодом `0`.
Frontend после этого пересобран и запущен. Это успешный прогон.
