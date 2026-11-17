<!-- docs/quality-environment-hotfix.md -->
# Изоляция quality gate и сохранение OCR-контекста ПЗ

База: `feature/quality`, `f757919fd0e6895cb95395fc0826b400ebe16358`.
В этом пакете нет изменений компоновки frontend, геометрии Review или n8n workflow.
Коммиты и push выполняет владелец.

## Первопричина 22 ошибок

В текущем `.env.example` включены `DOCUMENT_SERVICE_SPECIFICATION_SERVICE__ENABLED`
и `DOCUMENT_SERVICE_DOCLING__ENABLED`. Quality image копирует этот каталог настроек,
но намеренно не копирует приватный `.env`. Во время импорта HTTP-модулей создаётся
Document application, его Settings видят true и пустые ключи. Первичен ValidationError;
последующие ImportError `catalog_flow` / `pdf_flow` / `UnavailableVision` — каскад.

Воспроизведение на эквивалентном true-каталоге без ключей до исправления:
1 failed / 1 passed, изолированный вариант повторяет ошибку collection.
Ключи нельзя добавлять в тестовый образ для обхода этой ошибки.

## Изменения

- `ops/Dockerfile.quality`: отключены реальные Spec/OCR в ENV только тестового образа.
  `.env` остаётся исключённым из build context. В рабочем образе флаги не меняются.
- `ops/check-quality.ps1`: два переключателя сохраняются, временно устанавливаются
  в false для общих тестов и восстанавливаются в finally, включая аварийное завершение.
- `pyproject.toml`: явно задан function loop scope для async fixtures; устранено
  предупреждение root pytest о неустановленном asyncio_default_fixture_loop_scope.
- Production-валидация пустых ключей сохранена. Отдельный subprocess доказывает,
  что включённое приложение с публичными именами ключей нормально импортируется,
  при этом сетевые подключения запрещены тестом.

## Продолжение работы над качеством

Ранее контекст ПЗ снимался до Docling и оставался пустым на скане, хотя выбранная
страница уже получила OCR. Теперь готовый результат той же физической страницы
повторно используется для её пустой страницы ПЗ. Новый OCR/VLM-вызов не требуется.

Сохраняются номер страницы, исходный диапазон и маркер альтернативного чтения.
Надёжный native-текст не заменяется. Fallback-конверсия и страницы вне выбранного
анализа не подставляются как готовые. Граница OCR/native остаётся строгой: такое
чтение доступно модели, но не подтверждает само себя как буквальное доказательство PDF.

HTTP-регрессия проверяет настоящий PDF: первый лист без native-текста, второй
с надёжным текстом. Ответ extraction и контекст ПЗ содержат один и тот же OCR,
конвертер вызывается ровно один раз; второй лист сохраняется без изменений.

## Состояние сервера

17.11.2026 подтверждено наличие обоих ключей в собственной серверной `.env`;
их значения не выводились. В действующем Document-контейнере ещё нет ни ключей,
ни классов настроек новых интеграций. Это прежний образ, остановившаяся quality-
проверка не выполнила пересборку. После доставки исправления нужен штатный up/recreate,
обычный restart не обновляет образ и окружение.

## Проверки

- Изоляция quality, production-конфигурация с ключами, восстановление флагов
  PowerShell при успехе и ошибке: 5 passed.
- HTTP выбора PDF и сквозная граница OCR/ПЗ: 8 passed.
- Полный Windows gate: 3491 passed / 133 skipped; JavaScript 407/407.
- Полный Linux gate: 3490 passed / 134 skipped; JavaScript 407/407.
- Изолированные PostgreSQL-наборы Linux: Gateway 6, Knowledge 43, Experience 49,
  User 31, Auth 14, Equipment 159 passed (всего 302).
- Настоящие pointer resize/move, сохранение CAS и reload Review в отдельном
  headless Edge: 1 passed, 9,67 с. Рабочий Chrome не затрагивался.

Полные успешные журналы находятся в `.codex-test-temp/quality-hotfix-2026-11-17/`;
исходные документы, ключи, логи и временные артефакты не включаются в коммит.

Эти проверки не подменяют серверный анализ и не доказывают цели 3–5 минут или
50–70% инженерного покрытия. Радикальная оптимизация N-first и измерение актуальных
матриц остаются отдельной незакрытой приёмкой.

## Windows → Git → Linux

Windows, из корня. Ключи менять или копировать в `.env.example` не требуется:

```powershell
.\ops\check-quality.ps1
if ($LASTEXITCODE -ne 0) { throw 'Quality gate не прошёл' }
$files = Get-Content docs/quality-environment-hotfix-files.txt | Where-Object { $_ -and -not $_.StartsWith('#') }
git add -- $files
if ($LASTEXITCODE -ne 0) { throw 'Ошибка добавления файлов' }
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Ошибка staged diff' }
git diff --cached --stat
git commit -m "fix: изолировать quality от рабочих ключей и сохранить OCR-контекст ПЗ"
if ($LASTEXITCODE -ne 0) { throw 'Коммит не создан' }
git push https://github.com/Amadu-A/PDRD-validation.git HEAD:feature/quality
if ($LASTEXITCODE -ne 0) { throw 'Публичный push не выполнен' }
git push https://github.com/neo-term-it/PDRD-validation.git HEAD:feature/quality
if ($LASTEXITCODE -ne 0) { throw 'Приватный push не выполнен' }
```

Linux. В `.env` сохраняются оба выделенных ключа и включённые рабочие флаги:

```bash
cd ~/projects/PDRD-validation
mkdir -p .codex-test-temp/quality-hotfix
git pull --ff-only https://github.com/neo-term-it/PDRD-validation.git feature/quality || exit 1
bash ops/check-quality.sh > .codex-test-temp/quality-hotfix/quality.log 2>&1 || {
  tail -n 100 .codex-test-temp/quality-hotfix/quality.log
  exit 1
}
bash scripts/up.sh > .codex-test-temp/quality-hotfix/up.log 2>&1 || {
  tail -n 100 .codex-test-temp/quality-hotfix/up.log
  exit 1
}
bash scripts/check-stack.sh || exit 1
docker compose images document-service analysis-service api-gateway
```

Проверить загрузку новой конфигурации без вывода ключей:

```bash
docker compose exec -T document-service python - <<'PY'
from pdrd_document_service.core.settings import Settings
s = Settings()
assert s.specification_service.enabled and s.docling.enabled
assert s.specification_service.api_key.get_secret_value()
assert s.docling.api_key.get_secret_value()
print('Spec/Docling enabled; both dedicated keys configured')
PY
```

## Проверка руками и откат

1. Открыть frontend, проверить окна входа/регистрации и существующий Review:
   рамки/карточки двигаются, меняют размер и восстанавливаются после reload.
2. Скачать automatic PDF без решений Review и Reviewed PDF после утверждения.
   Красные рамки, линии и правый sidebar PDF должны сохраняться.
3. Запустить анализ с ПЗ, D и EQ: позиции спецификации не исчезают; скан выбранной
   страницы ПЗ получает уже выполненный OCR, надёжная текстовая ПЗ не переписывается.
4. Удаление Gold по-прежнему не требует причины и не создаёт Bad-пример Experience.

Workflow не изменён, новая публикация n8n для этого пакета не требуется.
Откат: один revert-коммит этого пакета, повторная доставка и штатный startup.
Тома/БД/исходники/Review не удаляются; схема данных и нормативные поколения не меняются.
