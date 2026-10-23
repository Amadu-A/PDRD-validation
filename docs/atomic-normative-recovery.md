# Восстановление атомарного нормативного каталога

Для двух больших исчерпанных failed-поколений применяется отдельная
[процедура с SQL-порцией 20](atomic-normative-batch-recovery.md): адресный административный
повтор сохраняет старую историю и не запускает общий backfill готовой базы.

## Причина и границы исправления

В присланной серверной диагностике 285 нормативных документов в 17 разделах:
284 имеют готовый chunk index, один — `failed`. Активный Qdrant alias содержит
28147 точек, включая 846 точек ИОС2. При этом таблицы атомарных поколений,
требований и свидетельств нормативного статуса пусты. Это отсутствие корпуса,
а не результат проверки «нарушений нет».

Прежний chunk worker фиксировал `READY`, а затем отдельной транзакцией вызывал
atomic queue. Падение между этими действиями теряло намерение. Повтор READY
выходил раньше постановки. Документы, обработанные до появления atomic index,
не имели автоматического backfill. Процесс outbox не восстанавливал эту цепочку.

Теперь `READY` и atomic intent входят в одну UOW. В ту же транзакцию входит
событие существующего outbox, если свободен admission slot. При заполненном
лимите сохраняется `dispatch_pending=true`; dispatcher допускает такое намерение
после освобождения места. CLI не запускает extractor внутри процесса анализа.
Повтор READY не обращается к embedding/Qdrant.

Существующие таблицы, RabbitMQ, Celery task и очередь сохранены. Миграция схемы
не требуется. Дополнительная информация хранится в существующем JSON payload.
Версия извлечения `normative-native-ocr-v3` отражает новые manifest/provenance и
выборочное OCR-происхождение. Исторические поколения не удаляются.

## Защита поколений и очереди

- Идентичность: документ + SHA оригинала + extractor version + digest журнала статуса.
- Глобальный PostgreSQL transaction advisory lock защищает admission между процессами.
  По умолчанию допущено не более двух queued/building заданий на весь каталог.
- Существующая section shared lock защищает удаление; отдельный ключ document
  advisory lock исключает параллельное извлечение разных попыток одного документа.
- `attempt_id` запрещает старой попытке писать, публиковать или менять статус новой.
  Изменение исходника, extractor или свидетельств отменяет незавершённый старый intent.
- Потерянная доставка восстанавливается после stale timeout; неопубликованный
  outbox не дублируется. Попытки worker и повторные доставки ограничены, временные
  отказы имеют backoff. Ошибки оригинала, OCR и целостности остаются диагностируемыми.
- Повтор очищает только неопубликованную рабочую область. Корректный active
  остаётся до полного commit нового поколения; повреждённый ready чинится отдельным
  repair generation, без удаления прежних строк.
- Перед публикацией проверяются реальные строки: count, уникальность IDs, SHA,
  document_id, версия manifest, физические страницы, буквальные смещения/цитаты,
  JSON и потоковый digest. Чтение через Analysis повторно проверяет digest корпуса.
- Непредвиденная программная ошибка не превращается в пустой успешный результат.

`dry-run` и `status` не записывают БД/outbox и не создают задачи. Они полностью
проверяют сохранённые поколения. Последующие polling-проходы не перечитывают весь
корпус; dispatcher проверяет готовые поколения ограниченной порцией по кругу.

## Форматы, OCR и ограничения поддержки

PDF читается из сохранённого оригинала с проверкой SHA. Для DOC/DOCX проверяется
sidecar `<original>.preview.pdf.source.json`: SHA оригинала и PDF. Старый preview
без подтверждённой связи пересоздаётся существующим LibreOffice converter.
Атомарная замена разрешена storage-портом только для preview/sidecar, не оригинала.

Atomic worker применяет существующий Tesseract CPU processor только к страницам
без native text, содержащим графику. Native страницы не рендерятся ради OCR.
По умолчанию не более 10 OCR-страниц на документ; timeout ограничен также stale
budget. Установка `MAX_OCR_PAGES=0` отключает OCR. VLM/GPU не используется.
OCR-атомы имеют `needs_review` и `ocr_text_requires_review`; OCR не становится
программным доказательством нарушения. Непрочитанные страницы, ошибки OCR и лимит
сохраняются в manifest/report. Полностью непрочитанный документ не публикуется;
частично извлечённый имеет `coverage=incomplete` с точными номерами страниц.

`requirements_supported` в каталоге — консервативная нижняя граница простой
числовой грамматики. `support_coverage` указывает это явно; остальные атомы
не теряются. `requirements_unsupported` отдельно считает атомы, закрытые текущим
guard интерпретации; оставшиеся `requirements_support_unassessed` не объявляются
ни поддержанными, ни неподдержанными заранее. Это не измерение фактически
проверенных проектных решений.
`actually_checkable` требует фактов проекта, применимости и нормативного статуса.
Точные результаты текущего Analysis выводятся отдельно в `evaluation_counts`:
total / selected / supported / unsupported / evaluated / violations_confirmed /
insufficient_evidence / applicability_unknown. Несколько объектов одной нормы
не увеличивают число исходных требований.

Отсутствие правовых свидетельств не мешает извлекать атомы, но не позволяет
подтвердить обязательное нарушение. Учебные свидетельства из тестов запрещено
переносить в рабочий каталог. Уполномоченный оператор может использовать существующий
`POST /api/v1/normative/documents/{id}/status-evidence`: редакция, область пунктов,
область применимости, URL, цитата и даты обязательны; автор берётся из сессии Gateway.

## CLI и API

В Knowledge runtime установлен `normative-requirements`; эквивалентный вызов:

```bash
python -m pdrd_knowledge_service.transport.cli.normative_requirements backfill --help
```

Область: ровно один `--all-sections`, `--section-id UUID`, `--document-id UUID`.
Команда всегда сохраняет корректные ready поколения; `--only-missing` явно
документирует этот идемпотентный режим. `--retry-failed` дополнительно включает
допустимые неудачные попытки с учётом backoff/лимита. Постоянные ошибки и исчерпанные
попытки не повторяются бесконечно. `--max-in-flight` может только уменьшать общий
настроенный предел. `--batch-size` ограничивает один проход постановки.

По умолчанию CLI ждёт terminal states и сохраняет отчёт на каждом проходе.
`--no-wait` выполняет одну ограниченную итерацию. `--poll-seconds` по умолчанию 5,
`--timeout-seconds` 7200. Повтор команды продолжает работу по сохранённой БД.
Коды: 0 — завершено без технических ограничений (либо read-only отчёт),
1 — завершено с failed/not-ready/partial документами, 2 — ожидание ещё не завершено.
Код 1 нельзя выдавать за полную готовность всех нормативов.

`GET /api/v1/normative/sections/{id}/atomic-readiness` возвращает лёгкую сводку
по документам/разделу. Gateway сохраняет существующую проверку доступа к разделу.
Новая сводка загружается независимо от основного каталога; её отказ не блокирует
выбор раздела, авторизацию или форму анализа.

В результате Analysis и UI `normative_coverage=incomplete` /
`normative_atomic_check=unavailable` явно обозначают отсутствующий или неполный
корпус. Доступные N/T/U/D/PZ/E/EQ результаты сохраняются. Нулевое число замечаний
не считается подтверждением полного нормативного соответствия.

## Настройки и последовательность поставки

Настройки `KNOWLEDGE_SERVICE_NORMATIVE_REQUIREMENTS__*` находятся в `.env.example`:
`RECONCILIATION_ENABLED=false`, interval 60 с, batch 2, max in-flight 2,
max attempts 3, stale 600 с, backoff 60 с, max OCR pages 10.
Восстановление зарегистрированных intent работает и при выключенном массовом
reconciliation. Флаг включает обнаружение документов, для которых intent ещё нет.

Windows, после просмотра изменений:

```powershell
.\ops\check-quality.ps1 -Fix
if ($LASTEXITCODE -ne 0) { throw "Quality gate failed" }
git diff --stat
git add -- (Get-Content .codex-test-temp/atomic-recovery/changed-files.txt)
git diff --cached --stat
git commit -m "fix: restore atomic normative indexing and N-first readiness"
git push https://github.com/Amadu-A/PDRD-validation.git feature/quality
git push https://github.com/neo-term-it/PDRD-validation.git feature/quality
```

Если Windows блокирует запуск локального ps1, используйте только для этого
процесса `powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\check-quality.ps1 -Fix`.
Системная execution policy при этом не меняется.

Linux — сначала обычная проверка/пересборка. Команды не удаляют нормативные тома
и не переиндексируют Qdrant ради атомов:

```bash
cd ~/projects/PDRD-validation
git pull --ff-only origin feature/quality
bash ops/check-quality.sh > atomic-quality-linux.log 2>&1
bash scripts/up.sh > atomic-up.log 2>&1
docker compose ps
docker compose exec -T knowledge-service python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8401/health/ready", timeout=15).status)'
docker compose exec -T analysis-service python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8501/health/ready", timeout=15).status)'
```

После успешного readiness — контролируемое восстановление. Shared n8n workflow
не менялись: повторный **Import → Save → Publish не нужен**.

```bash
docker compose exec -T knowledge-service normative-requirements backfill --all-sections --dry-run --report /data/normative/atomic-reports/dry-run.json
docker compose exec -T knowledge-service cat /data/normative/atomic-reports/dry-run.json > atomic-dry-run.json
# Берём один существующий READY норматив ИОС2 из динамического отчёта, а не зашитый UUID.
pilot_id="$(docker compose exec -T knowledge-service python -c 'import json; r=json.load(open("/data/normative/atomic-reports/dry-run.json")); s={x["section_id"] for x in r["sections"] if "ИОС2" in x["name"].upper().replace(" ", "")}; print(next(x["document_id"] for x in r["documents"] if x["section_id"] in s and x["eligible"]))')"
docker compose exec -T knowledge-service normative-requirements backfill --document-id "$pilot_id" --only-missing --max-in-flight 1 --report /data/normative/atomic-reports/pilot.json
docker compose exec -T knowledge-service python -c 'import json,sys,urllib.request; d=sys.argv[1]; b="http://127.0.0.1:8401/internal/v1/normative/documents/"+d; i=json.load(urllib.request.urlopen(b+"/requirements/index")); r=json.load(urllib.request.urlopen(b+"/requirements?limit=1")); assert i["state"]=="ready" and r["count"]>0; print(json.dumps({"state":i["state"],"generation":r["generation_id"],"count":r["count"],"manifest":r["manifest"],"first_requirement":r["requirements"][0]},ensure_ascii=False))' "$pilot_id"
```

Если пилот завершился с кодом 1/2, сначала прочитайте его report: нельзя продолжать
массовый запуск, игнорируя ошибку. Сравните Qdrant alias/число точек с диагностикой
до поставки. После успешного пилота:

```bash
backfill_status=0
docker compose exec -T knowledge-service normative-requirements backfill --all-sections --only-missing --retry-failed --max-in-flight 2 --batch-size 2 --wait --report /data/normative/atomic-reports/backfill.json || backfill_status=$?
printf 'Backfill exit code: %s\n' "$backfill_status"
docker compose exec -T knowledge-service cat /data/normative/atomic-reports/backfill.json > atomic-backfill.json
docker compose exec -T knowledge-service normative-requirements backfill --all-sections --status --report /data/normative/atomic-reports/status.json
docker compose exec -T knowledge-service cat /data/normative/atomic-reports/status.json > atomic-status.json
```

Отчёт должен перечислить все 17 актуальных разделов, включая пустые, и каждый
документ с причиной отсутствия полного корпуса. Не считать известный 285-й FAILED
документ успешно обработанным. Не подтверждать неизвестный правовой статус ради
зелёного отчёта. После проверки отчёта можно включить в серверном `.env`
`KNOWLEDGE_SERVICE_NORMATIVE_REQUIREMENTS__RECONCILIATION_ENABLED=true` и повторить
`bash scripts/up.sh`; появление пропущенных intent будет восстанавливаться фоном.

Qdrant можно проверить тем же способом до/после без вывода текста нормативов:

```bash
docker compose exec -T knowledge-service python -c 'import json,urllib.request; from pdrd_knowledge_service.core.settings import get_settings; s=get_settings(); b=s.qdrant.base_url.rstrip("/"); a=s.qdrant.normative_collection; print(json.dumps({"aliases":json.load(urllib.request.urlopen(b+"/aliases")),"catalog":json.load(urllib.request.urlopen(b+"/collections/"+a))},ensure_ascii=False))' > atomic-qdrant-after.json
```

До/после сравниваются alias, physical collection и points_count; наличие фоновых
обычных загрузок нужно учитывать отдельно. Backfill не вызывает vector store.

## Ручная приёмка и пределы доказанного

1. Открыть frontend под обычным пользователем: вход/регистрация, выбор раздела и
   загрузка документов работают. Поиск READY отдельно от N-first/числа атомов и
   правового статуса; предупреждение неполного корпуса видно до анализа.
2. Загрузить небольшой новый PDF/DOCX: после chunk READY появляется atomic queue,
   затем поколение/count. Не требуется повторная загрузка ради атомов.
3. Повторить проблемный полный PDF с прежними флагами контекста и сохранённым ТЗ,
   если он был в исходном задании. В диагностике проверить generation/SHA/count,
   source_complete, supported/unsupported/evaluated и причины непроверяемых норм.
4. На доказанно применимой настоящей норме провести положительный и отрицательный
   контроль с теми же объектом/условиями. Проверить N-документ, пункт, страницу и
   цитату в карточке и скачанном PDF. Без подтверждённого нормативного статуса
   подтверждённое нарушение не является допустимым ожидаемым ответом.
5. В Review проверить создание/редактирование/удаление, изменение обеих областей,
   сохранение после перезагрузки, утверждение и Automatic/Reviewed PDF.

Мок-контроль использует настоящие application/HTTP/Finalization и записи каталога,
но учебное нормативное свидетельство. Он не заменяет серверный E2E и не измеряет
совпадения H01–H35. Результаты текущих прогонов и точные ограничения фиксируются
в сопутствующем отчёте; если серверный доступ отсутствует, выполнение production
backfill и повторного анализа остаётся непроверенным.

Измерения текущей проверки: [отчёт с результатами и ограничениями](atomic-normative-recovery-evaluation.md).
