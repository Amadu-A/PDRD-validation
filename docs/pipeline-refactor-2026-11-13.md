<!-- docs/pipeline-refactor-2026-11-13.md -->
# Рефакторинг pipeline: причины, изменения и проверка

База: `feature/quality`, `519c48485932007cfb9fa3b84b2bf506edfba327`.
Контракты сверены с shared `839d419`, Spec Extracting AI `9844c4e` и OpenAPI
работающих Spec 1.0.0 / Docling Serve 1.38.2. Коммиты и push выполняет владелец.
Незавершённые пункты ниже не считаются приёмкой.

## Подтверждённые причины

1. Сохранённое задание `45f633de-df6e-48cb-bc91-32d419329eb4`: исходный PDF
   1 883 225 байт / 31 страница; JSON экспорта 1 101 903 байта. Document Service
   ответил `400 {"detail":"Part exceeded maximum size of 1024KB."}` до вызова
   PyMuPDF. Причиной этого инцидента не были CropBox или FreeText.
2. В исходном интервале Analysis зарегистрировал 15 HTTP 503. Сам shared-vlm
   пишет `Max queued tokens reached - rejecting request`: лимит 131072,
   наблюдавшийся вес, например, 144406. Подтверждено переполнение очереди токенов;
   это не исключает других причин 503 в иных запусках.
3. Захват перемещения был подключён только к карточкам, теперь также к рамкам
   ошибок. Совпавшие рамки Gold/VLM могут иметь общий hit target; это следует
   отличать от блокировки Review или потери CAS.

## Изменения

| Ранее | Сейчас | Проверка |
|---|---|---|
| JSON PDF в текстовом поле multipart | JSON-файл `payload`, явный предел 32 МБ; небольшое старое поле поддерживается | HTTP >1 МБ и рендер сохранённого задания |
| Общий отказ PDF | Типы ошибки, стадия, X-Request-ID; вход/генерация/недоступность/тайм-аут различаются | Секретный текст не раскрывается в ответе или журнале |
| Лимит только числа VLM-вызовов | Существующий лимитер учитывает оценку prompt и изображения в prefill | Вес, отмена, сохранность полного входа; grammar/output budget не считаются prefill |
| Разрозненные метрики | Интервалы в `metrics.execution_profile`, сводка Gateway с реальным job_id | Параллельный wall не суммируется; неизвестные токены остаются null |
| Understanding metrics терялись при сборке | Сохраняются в `understanding.metrics` | Требуется публикация PDF workflow |
| Локальный состав спецификации | При включении состав даёт Spec; PyMuPDF отдельно доказывает геометрию | REST, идемпотентность, строки, warnings, отмена, собственный кэш |
| Позиция без области не проходила контракт | `geometry_unresolved`, отдельные ID, все исходные поля | Повторные названия не объединяются; Brand/Article не угадываются |
| Нет выборочного Docling | Скан/сложная таблица через существующий CPU API | Отдельная физическая страница, OCR-флаги, сохранение native |
| Удалённый embedding runtime оставался в зависимостях | Удалены зависимость, тестовый путь и переменные отдельного runtime | Общие architecture/quality проверки |

Новые БД, RabbitMQ, модели, n8n и GPU runtime не создаются. Shared и соседние приложения
не меняются. Qdrant, embedding identity и готовые нормативные поколения не перестраиваются.
Проверки N/T/U/D/E/EQ и статусы доказательств не отключаются.
Координаты Review остаются 0..1000 исходного изображения; PDF sidebar в Review не сохраняется.

## Контракты и хранение

Spec: `POST /api/v1/extractions`, `GET /api/v1/extractions/{job_id}`,
`GET .../result.json`, постраничный `GET .../warnings`, `POST .../cancel`.
Выделенный `X-API-Key`; физические страницы и подтверждённый шифр передаются явно.
Ключ готового кэша зависит от SHA PDF, страниц, шифра, провайдера и версии кэша.
Idempotency-Key сохраняется внутри всех транспортных повторов одной операции.
Новая попытка после failed/cancelled получает другой ключ и не застревает на старом job_id.
Полные JSON/warnings атомарно сохраняются в собственном томе PDRD с digest до возврата
клиенту, независимо от внешнего TTL. Частичный результат не считается полным.
Изменение CACHE_VERSION инвалидирует повторное использование. Чужая БД не читается.
Опубликованный Spec API не выдаёт default prompt через API-ключ (HTTP 403), поэтому
при изменении внешнего default prompt/model оператор меняет CACHE_VERSION явно.

Docling: `POST /v1/convert/file/async`, `GET /v1/status/poll/{task_id}`,
`GET /v1/result/{task_id}`, `X-Api-Key`. Отправляется один физический лист; отображение
страницы конверсии 1 на исходный номер сохраняется. Force OCR выключен.
В опубликованном API нет cancel endpoint: локальная отмена прекращает ожидание,
внешняя работа ограничена document_timeout. Неизвестный POST после потери ответа
не повторяется; чтение состояния повторяется ограниченно. Полный результат сохраняется.

Настройки читаются только через Settings PDRD. Ключи других проектов не читались.
Выделенные ключи из собственной `.env` PDRD проверены реальными REST-запросами;
секреты не выводились и не включаются в Git. По умолчанию интеграции выключены;
включение без ключа отклоняется. Ключи нельзя передавать в браузер или чат.
Compose volume `document_extraction_artifacts`: `/data/extraction-artifacts/specification`
и `/data/extraction-artifacts/docling`. У исходных PDF и истории прежние владельцы.
Нативная геометрия сохраняется отдельным собственным артефактом с digest; n8n
получает `native_geometry_artifact_key` вместо повторного полного массива.
Скановый текст и непустые ячейки Docling с индексами строк/столбцов доступны следующему
анализу. Альтернативное чтение помечено, не объявляется доказанной идентичностью
и не заменяет надёжный native-текст. Полный JSON с bbox остаётся в собственном кэше.
Альтернативные секции `DOCLING_ALTERNATIVE` / `DOCLING_OCR_ALTERNATIVE` остаются
доступны модели, но исключаются из буквального подтверждения нативного слоя
в реестре решений, evidence audit и детекторе защищённых повторных обозначений.
Скан или альтернативная таблица не могут подтвердить сами себя. Исходные native-
цитаты и все альтернативные факты сохраняются; проверка изображения остаётся необходимой.

На Docling Serve 1.38.2 выявлено расхождение с описанием `ocr_lang` в OpenAPI:
`ru/en` отвергаются Tesseract, установленному runtime нужны `rus/eng`.
Это проверено на синтетическом русском скане; default PDRD соответствует факту runtime.
При точной привязке сохраняются доказанные native-модель, артикул и параметры,
даже когда исходные Brand/Article внешнего экспорта пусты. Эти исходные поля не
переписываются. Иное исполнение/артикул не получают чужую область; количество
сравнивается численно: 150,0 = 150.0 = 150, но не 1500. Неопределённая геометрия
не удаляет позицию. Пунктуация технического обозначения не выкидывается.

## Фактические проверки и открытая приёмка

- Сохранённое задание отрендерено PyMuPDF 1.28.0 за 4,38 с. Затем та же нагрузка прошла
  новый HTTP-адаптер Gateway → Document HTTP → PyMuPDF за 4,58 с: 78 аннотаций по физическим
  листам, 4 530 383 байта, 75 страниц с полным отчётом. Это не повторный VLM-анализ.
  Контрольные страницы: 8, 13, 18, 20, 24, 26.
- Все 31 исходные области в экспортированном PDF совпали с оригиналом пиксель в пиксель
  при рендере 72 dpi без аннотаций. Sidebar и линии не записываются в Review.
- Первые PDF-регрессии: 34 passed; расширенные Document/specification: 146 passed.
- Новые PDF/Spec/Docling/SHX/prompt/performance регрессии: 37 passed; после добавления
  классификации HTTP-ошибок — 20 passed в соответствующем наборе; метрики — 5 passed.
- Полный Windows gate 17.11.2026: 3478 passed / 133 skipped и 407 JS passed.
- Полный Linux gate 17.11.2026 прошёл, включая все шесть изолированных PostgreSQL
  наборов; размеры и итог дополнительных прогонов после последних уточнений — ниже.
- CRLF в `.env.example` воспроизвёл восемь сбоев Bash preflight в первом Linux gate.
  Файл приведён к LF, JSON OCR-языков заключён в shell-кавычки; повторный gate прошёл.
- Отдельный headless Edge проверил resize/move рамок и карточек VLM/Gold, CAS,
  reload и изменение ширины окна: 1 passed за 7,70 с на финальном коде.
  Рабочий Chrome/AITIM не затрагивался.
- Табличная упаковка промптов сократила страницы 8/13/26 лишь на 2,6–3,1%.
  Эксперимент откатан; новая форма фактов в runtime не вводится.
- Реальный Spec job `9f5968ea-dbbb-4811-bfad-b241645b5d34`: completed_with_warnings,
  20 строк, одно предупреждение, 27,62 с. Все восемь исходных строковых полей сохранены.
  Шифр `777/155-1с` прочитан из явной подписи исходного PDF. После строгой привязки:
  6 строк с геометрией / 14 geometry_unresolved, 20 EQ-позиций и 42 факта D.
- 17.11.2026 этот состав прошёл настоящий Understanding: 20 строк / 20 EQ / 42 факта
  Spec сохранены, один VLM-вызов за 15,8 с добавил 12 нетабличных фактов (штамп и
  примечания В1/В2). `specification_rows` и `equipment_identities` не запрашиваются
  повторно для готового состава. Счётчик `programmatic_facts` включает `spec_service`,
  эти 42 факта не записываются в `vlm_facts`.
- На странице 26 PyMuPDF ранее видел только 70 символов. Теперь читается также исходный
  `AutoCAD SHX Text` (не Review-аннотации): 2302 символа. Геометрия берётся из реальных
  прямоугольников, проверяются физическая строка, единица и количество. `110x10` не
  совпадает с `110x100`; одинаковые позиции не склеиваются. TableFinder возвращает
  визуальные координаты повёрнутой таблицы; повторный rotation устранён.
- Реальный Docling p26 без OCR: success, 46,6 с, но одна почти пустая строка таблицы.
  Это проверка транспорта/конверсии, а не успешное извлечение всех 20 позиций.
  Состав таблицы подтверждён отдельным Spec-прогоном выше.
- Реальный русский скан без текстового слоя: Docling success, 2,28 с; текст
  «Противопожарный резервуар. Объём 100 м3.» распознан полностью.
- Три cold + три warm полного анализа и три актуальные экспертные матрицы требуют
  завершения. Unit/functional и стенд браузера не заменяют промышленный E2E.
- Цель cold 180–300 с не подтверждена. Токенный лимитер действует на один процесс
  PDRD, а не на все приложения shared-vlm; внешняя конкуренция измеряется отдельно.
- Provider duration содержит HTTP-время и внутреннюю очередь провайдера;
  gpu_wait_ms — очередь PDRD. Из HTTP duration нельзя вывести чистое GPU-время.
  Неизмеренный CPU остаётся null. Gateway связывает document_id профиля с реальным job_id.

## Что действительно измерено по производительности

Сохранённый baseline: 21 мин 16,94 с от создания до завершения задания.
Длительности запросов ниже — суммарная работа, **не wall time этапов**:

| Этап | Вызовы | Input tokens | Output tokens | Сумма provider, с |
|---|---:|---:|---:|---:|
| Understanding | 29 | 188318 | 43154 | 1453,19 |
| Normative Check | 31 | 1111846 | 22663 | 2194,54 |
| Finalization | 14 | 99586 | 4466 | 301,34 |

Разрешение очереди vLLM проверено по коду действующего контейнера: лимит касается
prompt-токенов запросов в prefill. Он не включает output budget и schema grammar.
Локальная оценка PDRD: UTF-8(prompt)/3 плюс резерв изображения; default 100000.
Это приблизительный допуск на один процесс, а не общий менеджер GPU и не точная
токенизация. Другие приложения могут заполнить внешнюю очередь; существующие
ограниченные retry/Retry-After и сохранение успешных страниц продолжают действовать.

Компонентный замер: страницы 13/20/26, один discovery batch, 4000 output tokens,
одинаковые полные N/T/U/D-входы и изображения, PDRD cache выключен:

| Лимит вызовов | Токенный бюджет | Wall, с | Физические попытки | Findings по страницам |
|---:|---:|---:|---:|---|
| 1 | 0 | 28,66 | 3 | 1 / 1 / 0 |
| 4 | 0 | 14,82 | 3 | 1 / 1 / 0 |
| 4 | 100000 | 14,79 | 3 | 1 / 1 / 0 |

Между вариантами нет очистки provider prefix cache. Таблица не является ни cold/warm
приёмкой, ни оценкой покрытия человеческих замечаний. Она подтверждает работу
реального REST-контракта и отсутствие регрессии на этих трёх запросах.

В промпте удалён только буквальный дубль `D.text == D.evidence_text`, с явным
`text_same_as_evidence`. Различающиеся тексты, ID, страницы, условия и области сохраняются.
Этот небольшой выигрыш нельзя выдавать за радикальное ускорение N-first.

Единый отчёт `result.performance` содержит интервалы этапов, logical/physical calls,
retries, tokens, cache hits/misses, объёмы контекста, ожидание PDRD и provider work.
После retention `updated_at` уже не считается временем завершения, `total_wall_ms=null`.
Подготовка контекстов, чистое GPU-время, CPU отдельных coroutine и EQ/network пока не
разделены полностью. Изменение общей оркестрации и достижение 180–300 с не приняты.

`ios2_2_human_remarks_semantic_v2.json` и отдельная матрица 23 КИПиА в доступном
корпусе не найдены. Девять PDF/JSON в media содержат 424 включённых замечания,
в том числе ИОС2.2: 59 / rev2: 50; этот корпус нельзя выдавать за актуальную матрицу
16 исходных / 15 уникальных вопросов. В нём доступен ТМ `ИОС 4.1.7`: четыре случая
повтора `8.5.4`, `8.9.1`, `8.9.2`, `8.9.3`. На реальном исходном PDF программный
discovery нашёл все 4/4 на странице 1, без VLM и без входного массива замечаний.
Это проверка обнаружения и сохранения текущего детектора, не новое измерение
Recall всей системы или финализированного серверного задания.
Существующий `scripts/evaluate_human_remarks.py` остаётся средством
проверки настоящего API JSON и явных семантических решений.

## Итог проверок 17.11.2026

| Проверка | Результат |
|---|---|
| Windows `ops/check-quality.ps1` | 3478 passed, 133 skipped; JS 407/407 |
| Linux общий набор | 3479 passed, 132 skipped; JS 407/407 |
| Linux Gateway / Knowledge / Experience | 6 / 43 / 49 passed |
| Linux User / Auth / Equipment | 31 / 14 / 159 passed |
| Финальные Windows Analysis/Document + инженерные functional | 968 passed |
| Финальные Linux Analysis/Document + OCR trust | 893 passed |
| Отдельный headless Edge: настоящие pointer-события, CAS, reload | 1 passed |
| Ruff по всему проекту после последних изменений | Passed; формат проверен |
| OCR-повторы и сохранение нативных повторов, Windows / Linux | 17 / 17 passed |

После общих gate уточнялись только счётчики Spec, строгая native-привязка и граница
OCR/native. Они повторно проверены полными наборами затронутых сервисов и предметными
functional-тестами; тесты изолированных БД не заменяются моками. Последняя регрессия
OCR-повторов повторно проверена вместе с соответствующим набором на Windows и Linux.
Логи и тестовые PDF находятся в `.codex-test-temp/refactor-2026-11-13/`, в Git не включаются.
Сервер 17.11.2026 остаётся на `519c4848`; все 19 PDRD-контейнеров healthy.
Это readiness предыдущей поставки, не приёмка неразвёрнутого кода.

Открыты: радикальное сокращение N-first-контекста и перестройка зависимостей pipeline,
полное раздельное измерение PDF/OCR/EQ/CPU, три cold + три warm, финальная серверная
проверка PDF/Review и семантическое сравнение актуальных матриц. Цели 180–300 с и
50–70% покрытия не объявляются выполненными. ПЗ из непроанализированных скановых
страниц по-прежнему требует отдельной проработки; надёжный native-контекст ПЗ не заменяется.

## Поставка и откат

После завершения проверок поставляется один подготовленный коммит. Изменён
`n8n/workflows/analysis-v2-pdf.json`: PDF workflow требует Import → Save → Publish.
CAD/PDF+CAD workflow не заменяются PDF workflow.

Ключи уже выданы владельцем. Для серверной поставки в `.env` PDRD включаются
соответствующие ENABLED. `.env.example` содержит только несекретные defaults и пустые ключи.
Миграций БД для этих изменений нет. Document/Gateway пересобираются согласованно:
новый Gateway передаёт payload файлом. Shared/Spec при поставке PDRD не перезапускаются.

Rollback возвращает предыдущие образы Document/Gateway/Analysis/frontend и предыдущую
публикацию PDF workflow. Собственные тома, исходники, Review, нормативные поколения
и состояние соседних приложений сохраняются. `docker compose down -v` не используется.

### Настройки серверной `.env`

Ключи `SPEC_EXTRACTING_AI_API_KEY` и `DOCLING_API_KEY` уже заданы и не копируются в
`.env.example`. Для реального маршрута добавить только два включателя:

```ini
DOCUMENT_SERVICE_SPECIFICATION_SERVICE__ENABLED=true
DOCUMENT_SERVICE_DOCLING__ENABLED=true
```

Публичный host URL Spec в каталоге — `SPEC_EXTRACTING_AI_BASE_URL=http://127.0.0.1:8083`.
В контейнере он заменяется `PDRD_SPEC_EXTRACTING_AI_INTERNAL_BASE_URL`, default
`http://spec-extracting-ai-api-1:8083`. Для Docling аналогично действует
`PDRD_DOCLING_INTERNAL_BASE_URL=http://docling-eval:5001`. Если сервис перенесён,
оператор меняет соответствующую INTERNAL_BASE_URL. Секреты остаются только в `.env`.
После редактирования `.env` нужен recreate Document, обычный restart не перечитывает
окружение контейнера. На сервере задача пока не развёртывалась.

### Windows: проверка и один коммит владельца

Из корня репозитория. Общие тесты используют моки; реальные внешние интеграции
проверяются отдельно. Временно отключаем их в окружении процесса, чтобы локальные
промышленные настройки не запустили внешнюю обработку из unit-тестов:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Не установлены dev-зависимости' }
$env:DOCUMENT_SERVICE_SPECIFICATION_SERVICE__ENABLED='false'
$env:DOCUMENT_SERVICE_DOCLING__ENABLED='false'
.\ops\check-quality.ps1
if ($LASTEXITCODE -ne 0) { throw 'Quality gate не прошёл' }
$env:PDRD_BROWSER_E2E='1'
.\.venv\Scripts\python.exe -m pytest -c pyproject.toml tests/functional/test_review_browser_geometry.py -q --basetemp .codex-test-temp/edge-release -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw 'Проверка Edge не прошла' }
Remove-Item Env:PDRD_BROWSER_E2E
Remove-Item Env:DOCUMENT_SERVICE_SPECIFICATION_SERVICE__ENABLED
Remove-Item Env:DOCUMENT_SERVICE_DOCLING__ENABLED
```

Тест запускает **отдельный headless Edge с новым профилем**, не подключается к
запущенным браузерам. Проверки не закрывают и не переключают рабочий Chrome/AITIM.
Для commit используется список [pipeline-refactor-files.txt](pipeline-refactor-files.txt),
включающий удаление прежнего embedding runtime. В нём нет `.env`, PDF, media, логов
и временных артефактов. Перед выполнением проверить `git status` и staged diff:

```powershell
$files = Get-Content docs/pipeline-refactor-files.txt | Where-Object { $_ -and -not $_.StartsWith('#') }
git add -- $files
if ($LASTEXITCODE -ne 0) { throw 'Файлы не добавлены' }
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Ошибка staged diff' }
git diff --cached --stat
git commit -m "fix: восстановить PDF и Review, подключить Spec и Docling"
if ($LASTEXITCODE -ne 0) { throw 'Коммит не создан' }
git push https://github.com/Amadu-A/PDRD-validation.git HEAD:refs/heads/feature/quality
if ($LASTEXITCODE -ne 0) { throw 'Публичный push не выполнен' }
git push https://github.com/neo-term-it/PDRD-validation.git HEAD:refs/heads/feature/quality
if ($LASTEXITCODE -ne 0) { throw 'Приватный push не выполнен' }
```

Эти команды выполняет владелец. Автоматического commit/push со стороны агента нет.
Пакет исправлений не является заявлением о выполнении открытых целей времени/качества.

### Linux: pull, тесты, пересборка и readiness

До поставки сохранить SHA и действующие image ID. Штатный startup сохраняет
проверки конфигурации, миграций и readiness; новые миграции в этом изменении не добавлены.

```bash
cd ~/projects/PDRD-validation
mkdir -p .codex-test-temp/release
git rev-parse HEAD > .codex-test-temp/release/previous-head.txt
docker compose images > .codex-test-temp/release/previous-images.txt
docker compose ps -q | xargs docker inspect --format '{{.Config.Image}} {{.Image}}' > .codex-test-temp/release/previous-image-refs.txt
git diff --quiet && git diff --cached --quiet || exit 1
git pull --ff-only https://github.com/neo-term-it/PDRD-validation.git feature/quality || exit 1
bash ops/check-quality.sh > .codex-test-temp/release/linux-quality.log 2>&1 || {
  tail -n 100 .codex-test-temp/release/linux-quality.log
  exit 1
}
bash scripts/up.sh > .codex-test-temp/release/startup.log 2>&1 || {
  tail -n 100 .codex-test-temp/release/startup.log
  exit 1
}
docker compose ps
docker compose images
```

Проверить новые переменные без вывода ключей и доступность локальных HTTP readiness:

```bash
docker compose exec -T document-service python - <<'PY'
from urllib.request import urlopen
from pdrd_document_service.core.settings import Settings
s = Settings()
assert s.specification_service.enabled and s.docling.enabled
assert s.specification_service.api_key.get_secret_value()
assert s.docling.api_key.get_secret_value()
print('Spec:', s.specification_service.base_url)
print('Docling:', s.docling.base_url, 'OCR:', s.docling.ocr_languages)
print('Document readiness:', urlopen('http://127.0.0.1:8301/health/ready').status)
PY
docker compose exec -T analysis-service python - <<'PY'
from urllib.request import urlopen
print('Analysis readiness:', urlopen('http://127.0.0.1:8501/health/ready').status)
PY
docker compose exec -T api-gateway python - <<'PY'
from urllib.request import urlopen
print('Gateway readiness:', urlopen('http://127.0.0.1:8000/health/ready').status)
PY
```

Затем импортировать **только PDF workflow** `n8n/workflows/analysis-v2-pdf.json`,
Save → Publish. Изменение сохраняет метрики Understanding в результате; остальные
workflow не заменяются. При поставке не перестраиваются Qdrant, embedding, VLM,
готовые нормативные поколения и соседние приложения.

### Ручная проверка после поставки

1. Открыть приложение; кнопки входа/регистрации открывают диалоги, нет 403 на JS/CSS.
   Для собственного read-only UI контроля агент использовал `http://192.168.55.3:8080/`
   в отдельном Edge. Настройки HTTPS Origin/Secure cookie авторизации не ослаблялись.
2. Сохранённое задание `45f633de-df6e-48cb-bc91-32d419329eb4`: автоматический PDF
   скачивается без запуска анализа и без завершённого Review.
3. Двигать и менять размеры рамки и карточки VLM/Gold, включая вторичные области;
   сохранить, переключить лист, изменить масштаб, перезагрузить. Все координаты
   восстанавливаются; чужой read-only доступ не получает редактирование.
4. Проверить edit/accept/reject и CAS-конфликт двух вкладок на тестовом задании.
   Удаление Gold не спрашивает причину и не создаёт Bad-пример Experience.
5. Итоговый Reviewed PDF недоступен до завершения решений; после утверждения
   содержит Wise/Edited/Gold, исключает Bad. Красные области и линии остаются,
   карточки находятся справа только в PDF. Полные обоснования — в ведомости.
6. Новый анализ с D/ПЗ/EQ: страница 26 содержит все 20 позиций. Неустановленные
   координаты обозначены `geometry_unresolved`, а не вымышленными рамками.
7. Повторить полный контрольный анализ трижды cold и трижды warm на одной версии
   PDF/нормативных поколений/настроек. Сохранить настоящий API JSON, профиль этапов,
   ошибки/ограничения и семантическую оценку матриц. Только это закрывает E2E-приёмку.

### Откат владельцем

Сохранённые `previous-head.txt` / `previous-images.txt` определяют точную предыдущую
версию. Для отката кода создать отдельный revert-коммит этого изменения и доставить
его тем же Windows → Git → Linux порядком. Для срочного runtime-отката восстановить
предыдущие image ID под соответствующими compose image names. До приёмки не удалять
прежние образы. Команды используют сохранённые ссылки своих контейнеров:

```bash
cd ~/projects/PDRD-validation
while read -r image_ref image_id; do
  docker image tag "$image_id" "$image_ref" || exit 1
done < .codex-test-temp/release/previous-image-refs.txt
docker compose up -d --no-build api-gateway api-gateway-worker api-gateway-outbox analysis-service document-service frontend review-frontend
docker compose ps
```

Затем выполнить readiness из блока поставки.
Вернуть предыдущую опубликованную версию PDF workflow. Не удалять тома и не
сбрасывать рабочие БД; новые JSON-артефакты остаются независимым кэшем.
