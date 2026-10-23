<!-- docs/quality-stage3-delivery.md -->

# Quality, этап 3: проверка и поставка одним коммитом

Рабочая ветка — `feature/quality`. До действий с Git просмотреть diff.
Агент не выполняет staging, commit, push или развёртывание рабочего стека.
Список [quality-stage3-files.txt](quality-stage3-files.txt) содержит точные пути этой поставки.
Имеющееся пользовательское изменение `.env.example` в него не включено и сохраняется в рабочем дереве.

## Windows PowerShell

Из корня репозитория, в существующем `.venv` (либо заменить путь на `.venv-dev`):

```powershell
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP = (Resolve-Path .codex-test-temp).Path
$env:TMP = $env:TEMP
function Assert-Exit { if ($LASTEXITCODE -ne 0) { throw "Код завершения: $LASTEXITCODE. См. лог." } }
& .\.venv\Scripts\python.exe -m ruff check . --fix *> .codex-test-temp\quality3-ruff.log
Assert-Exit
& .\.venv\Scripts\python.exe -m ruff format . *> .codex-test-temp\quality3-format.log
Assert-Exit
& .\.venv\Scripts\python.exe -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp ".codex-test-temp\quality3-$([guid]::NewGuid().ToString('N'))" *> .codex-test-temp\quality3-pytest.log
Assert-Exit
.\ops\check-quality.ps1 *> .codex-test-temp\quality3-gate.log
git diff --check
Assert-Exit
git status --short
git diff --stat
```

`check-quality.ps1` останавливается при ошибке, выполняет Ruff, pytest, frontend Node tests
и `git diff --check`. Отдельные Ruff/pytest выше нужны для самостоятельной диагностики;
после их успеха достаточно одного общего gate, повторные успешные прогоны не обязательны.
При ошибке читать соответствующий лог; не переходить к commit.

Явный staging и публикация после просмотра изменений:

```powershell
$qualityFiles = Get-Content -Encoding utf8 .\docs\quality-stage3-files.txt
git add -- $qualityFiles
Assert-Exit
git diff --cached --check
Assert-Exit
git diff --cached --stat
git commit -m "fix: preserve engineering evidence and review lifecycle"
Assert-Exit
git push git@github.com:Amadu-A/PDRD-validation.git HEAD:feature/quality
Assert-Exit
git push git@github.com:neo-term-it/PDRD-validation.git HEAD:feature/quality
Assert-Exit
```

В текущей локальной конфигурации `origin` уже имеет два push URL. Две явные команды выше
однозначно показывают получателей; дополнительный `git push origin` после них не требуется.
Не использовать `git add .`, force push или автоматическое включение временных PDF/логов.

## Linux

На сервере с настроенным private origin:

```bash
cd ~/projects/PDRD-validation
git status --short
git pull --ff-only
git diff --check
bash ops/check-quality.sh > /tmp/pdrd-quality3-linux.log 2>&1 || { tail -n 120 /tmp/pdrd-quality3-linux.log; exit 1; }
bash scripts/up.sh > /tmp/pdrd-quality3-up.log 2>&1 || { tail -n 120 /tmp/pdrd-quality3-up.log; exit 1; }
bash scripts/check-stack.sh > /tmp/pdrd-quality3-readiness.log 2>&1 || { cat /tmp/pdrd-quality3-readiness.log; exit 1; }
docker compose ps
```

При незакоммиченных серверных изменениях сначала разобраться с ними; не применять reset/clean.
`ops/check-quality.sh` уже включает шесть изолированных интеграционных Compose-наборов:
gateway, knowledge, experience, user, auth и equipment. Новый скрипт проверок не нужен.
`scripts/up.sh` штатно собирает сервисы, обновляет frontend и проверяет readiness.

Workflow n8n не изменены: **Import → Save → Publish не требуется**.
Разрушительная переиндексация не выполняется. Подготовка атомарного корпуса и проверка
пилотного документа описаны в [существующей инструкции](atomic-normative-recovery.md).
Результат начатой ранее переиндексации должен оцениваться отдельно от нового инженерного прогона.

## Ручная приёмка

1. Обновить frontend после деплоя. Проверить загрузку CSS/JS без 403/404, вход, выход и регистрацию.
2. Под admin открыть нормативный раздел: доступны подробности N-first, атомов и нормативного статуса.
   Под обычным пользователем и гостем — только краткие статусы документов. После выхода из admin
   подробности исчезают без перезагрузки; выбор раздела и запуск анализа продолжают работать.
3. Открыть существующее задание с Review. Мышью и клавиатурой изменить основную рамку VLM,
   карточку VLM, рамку Gold и карточку Gold. Переместить карточку, выполнить Undo, дождаться
   «Изменения сохранены», перезагрузить страницу и сравнить размеры/положение.
4. Добавить Gold двумя выделениями; изменить текст/нормативное основание, принять, отклонить,
   вернуть через Undo. Удаление/отмена Gold — без диалога причины. Утвердить Review и убедиться,
   что отменённый Gold не создаёт новую запись Experience. Причина для отказа VLM обязательна.
5. Проверить отказ VLM, его скрытие, Undo, текстовый список, независимость соседних findings,
   повторное сохранение после ошибки сервиса и запрет скачивания неутверждённого Review.
6. Скачать автоматический `/annotated-pdf` и утверждённый `/reviewed-pdf` отдельно. На повёрнутых
   листах карточки/номера читаются горизонтально. Сохранённая ручная геометрия остаётся на своём листе.
7. Запустить полный анализ ИОС2.2 с теми же флагами контекста. Проверить один H09 на 8/13/14,
   вопрос H16 с обеими ячейками 15/30, H24 и вопрос H10 с 147/150 м. Сверить реальные строки и bbox
   страницы 26: локальная модельная проба ещё не подтвердила точность этих координат.
8. Проверить отсутствие обратных конфликтов толщин, DN/Ø, разных участков, PDF/внутренних листов;
   не считать неполный Understanding или неподготовленный нормативный корпус успешной полной проверкой.

Полноценная серверная приёмка завершается после Linux gate, readiness, нового n8n-прогона,
проверки PDF/Review и инженерной оценки по [матрице](quality-stage3-evaluation.md).


## Результат локальной проверки

23.10.2026: финальный Windows quality gate — 3199 passed / 120 skipped (220,20 с),
405 frontend tests прошли. Ruff, форматирование, pip check и git diff --check прошли.
120 skips — внешние зависимости/изолированные интеграционные среды, не 120 доказанных успехов.
Лог: `.codex-test-temp/stage3-quality-release.log`.
Linux gate и readiness рабочего развёртывания пока не выполнены.
