<!-- docs/atomic-normative-batch-recovery.md -->

# Восстановление больших нормативов небольшими SQL-порциями

Дата: 23.10.2026. Ветка `feature/quality`; исходный HEAD
`c6c94ba0f810dc191d398ef3ba8015af3a01d8d7`. Удалённый `main`:
`8fd5adcdf1db9088f182f69bee7e7df1ef9ca2b1`.
Оба HEAD сверены через `git ls-remote`; локальные ссылки совпали.
Правила sibling `shared-infrastructure` сверены с его удалённым HEAD
`8b04d082424245372b54702939cc45b01abe389b`.

## Диагноз и границы исправления

Из промышленного отчёта подтверждён обрыв соединения asyncpg во время INSERT JSONB.
Причина закрытия соединения окончательно не установлена. Нет доказательства, что PostgreSQL
не способен обработать 100 строк: на изолированной БД нормальные порции 100 проходят.
Уменьшение порции — проверяемая мера ограничения размера операции, а не доказанный диагноз.

В существующем коде транзакции уже были разделены по порциям; отдельные session advisory locks
также фиксировали транзакцию до извлечения PDF. Однако размер use case был жёстко равен 100,
runtime его не передавал, а повтор отдельной SQL-транзакции отсутствовал.
Счётчик увеличивался до подтверждения commit. При добавлении retry без исправления этого места
можно было бы пропустить ordinal или дважды учесть атом в digest.

Изменено:

- `KNOWLEDGE_SERVICE_NORMATIVE_REQUIREMENTS__BATCH_SIZE=20`, диапазон 1–200.
  Настройка проходит Settings → worker DI → BuildNormativeRequirements.
  В Compose она явно включена в общий environment anchor, используемый `knowledge-indexer`.
- Каждая порция фиксируется отдельной UOW. Извлечение PDF/атомов не держит бизнес-транзакцию.
- При распознанном отключении PostgreSQL повторяется только текущая неподтверждённая порция:
  максимум три попытки, интервалы 0,5 и 1 с. Создаются новая UOW/session и новое рабочее соединение.
  Произвольные SQL-ошибки, нарушения целостности, таймауты и программные дефекты не повторяются этим механизмом.
- При потере ACK после успешного commit сначала сверяются сохранённые count и содержимое порции.
  Уже подтверждённая порция не вставляется повторно. Счётчик/digest меняются только после подтверждения.
- Сохранены детерминированные requirement_id, порядок ordinal, digest и обязательная проверка
  всех строк перед публикацией. SQL-пагинация дополнительно отвергает разрыв ordinal.
- JSONB ограничивается без обрезки: максимум 2 MiB сериализованного JSON на атом,
  8 MiB JSON-параметров на порцию. Если нужно, SQL-порция делится ещё мельче.
  Учитывается стандартное ASCII-экранирование JSON-сериализатора SQLAlchemy.
  Это предел параметров JSONB, а не всего сетевого пакета.
- `normative_atom_too_large` указывает requirement_id, фактические байты и лимит.
  `normative_batch_write_exhausted` указывает поколение, offset, строки, байты и число попыток.
  Оба отказа блокируют автоматический повтор полного поколения до диагностики.
- Логи успешных/повторных порций содержат offset, rows, json_bytes, attempt и duration_ms;
  исходный нормативный текст не выводится. SQLAlchemy скрывает SQL-параметры в исключениях.
- `backfill --document-id UUID --restart-exhausted` создаёт отдельное поколение только для
  текущего исчерпанного failed. Старое поколение, его строки, счётчики и причина отказа остаются.
  Новое хранит previous_generation_id, прежние счётчики и причину/время административного действия.
  Это контейнерный административный CLI; публичный HTTP-контракт не расширен этим флагом.
  Глобальная область и изменение ready запрещены. Автоматический dispatcher не использует этот режим.
- Отчёт status теперь показывает заявленный и реально проверенный digest, а фактическое количество
  строк — и для незавершённых/failed поколений, а не только ready.

Переподключение выполняется на границе UOW с независимым application-исключением;
application не импортирует asyncpg/SQLAlchemy. Закрытие session выполняется и при ошибке rollback,
а исходная ошибка/отмена не подменяется ошибкой cleanup. Семантика повторов соответствует
[правилам SQLAlchemy для отключений внутри транзакции](https://docs.sqlalchemy.org/en/20/core/pooling.html#disconnect-handling-optimistic).

Фронтенд, n8n, Qdrant runtime, embedding, VLM, исходные PDF и структура таблиц не менялись.
Миграция не нужна. Нужны пересборка/пересоздание Knowledge service, worker и dispatcher штатным up.sh.
Import → Save → Publish workflow n8n не нужен.

## Фактически выполненные проверки

Новые unit/regression: 23 теста, 2,23 с. Проверяются порции 1/10/20/100, потеря соединения до commit,
неоднозначный commit с потерей ACK, повреждённый ACK, ограничение retry/backoff, программное исключение,
крупный JSONB, слишком большой атом, DI/env/Compose и адресное восстановление двух поколений.
Тестовая библиотека содержит 283 ready + 2 exhausted failed: после повтора готово 285;
исходные 283 manifest/строки и история двух отказов остаются неизменными.
Это тест сохранности, не подтверждение промышленного результата.

Существующий изолированный `ops/compose.knowledge-test.yaml` реально запущен через Docker Desktop:
PostgreSQL 16, отдельный project `pdrd-atomic-batch-test`, внутренняя сеть и tmpfs.
Первый прогон — **41 passed / 8,43 с**, финальный с актуальным исходным кодом —
**41 passed / 9,42 с**. Миграции тестовой БД дошли до существующего `20261020_0009`.

В тесте соединение принудительно закрывается через pg_terminate_backend, когда INSERT
выполняет PgSleep внутри серверного trigger. PID берётся из самой тестовой UOW.
Подтверждено: нулевая порция выполняется один раз, порция offset=20 повторяется с другим PID,
дубликатов/пропусков нет, digest совпадает, поколение ready, attempt_count всего построения равен 1.
Также реально проверены большие JSONB, идемпотентная вставка и отказ публикации при разрыве ordinal.
После проверки удалены только созданные тестовые контейнеры и сеть.

Дополнительный финальный набор unit/recovery/HTTP: **53 passed / 44,03 с**.
Полный Windows gate: **3222 passed / 126 skipped**, 405 frontend tests прошли;
Ruff check/format, pip check и git diff --check прошли. Лог —
`.codex-test-temp/atomic-batch-quality-final.log`. Пропуски реальных внешних интеграций
не считаются успешной приёмкой; изменённый Knowledge PostgreSQL-набор выполнен отдельно.
Временные каталоги сервисов исключены из обоих Docker build context: иначе сборка на Windows
пыталась прочитать закрытый старый `.codex-test-temp` внутри analysis-service.

Production: SSH отклонил ключ. Развёртывание и два промышленных повтора агент не выполнял.
Состояние 285/285 пока не подтверждено. Присланный отчёт от 22.10 показывает 282 ready,
1 not_indexed и 2 failed; актуальный текст задания сообщает уже 283 ready и 2 failed.
Процедура ниже сверяет реальную базу перед запуском и не начинает переиндексацию третьего документа.

## Windows и Git

Из корня репозитория:

```powershell
$ErrorActionPreference = 'Stop'
function Assert-Exit { if ($LASTEXITCODE -ne 0) { throw "Ошибка: $LASTEXITCODE. См. лог." } }
.\.venv\Scripts\python.exe -m ruff check . --fix *> .codex-test-temp\atomic-batch-ruff.log
Assert-Exit
.\.venv\Scripts\python.exe -m ruff format . *> .codex-test-temp\atomic-batch-format.log
Assert-Exit
.\.venv\Scripts\python.exe -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp ".codex-test-temp\atomic-batch-$([guid]::NewGuid().ToString('N'))" *> .codex-test-temp\atomic-batch-pytest.log
Assert-Exit
.\ops\check-quality.ps1 *> .codex-test-temp\atomic-batch-gate.log
git diff --check
Assert-Exit
git diff --stat
$atomicFiles = Get-Content -Encoding utf8 .\docs\atomic-normative-batch-files.txt
git add -- $atomicFiles
Assert-Exit
git diff --cached --check
Assert-Exit
git diff --cached --stat
git commit -m "fix: recover large atomic normative batches safely"
Assert-Exit
git push git@github.com:Amadu-A/PDRD-validation.git HEAD:feature/quality
Assert-Exit
git push git@github.com:neo-term-it/PDRD-validation.git HEAD:feature/quality
Assert-Exit
```

Staging/commit/push агент не выполняет. Файлы перечислены явно, `git add .` не применяется.
Штатный Windows gate уже включает Ruff, format, pytest и Node; отдельные команды выше нужны,
если требуется самостоятельная диагностика, а не обязательные повторные успешные прогоны.

## Linux: проверка и развёртывание

Выполнять из существующего checkout с private origin. При ошибке следующий шаг не выполнять.

```bash
cd ~/projects/PDRD-validation
git status --short
git pull --ff-only origin feature/quality
git diff --check
bash ops/check-quality.sh > /tmp/pdrd-atomic-batch-quality.log 2>&1
bash scripts/up.sh > /tmp/pdrd-atomic-batch-up.log 2>&1
bash scripts/check-stack.sh
docker compose exec -T knowledge-indexer python -c 'from pdrd_knowledge_service.core.settings import get_settings; n=get_settings().normative_requirements.batch_size; print("SQL batch:",n); assert n==20'
```

Linux gate уже включает изолированный Knowledge PostgreSQL-набор вместе с остальными
интеграционными наборами. При необходимости отдельного воспроизведения:

```bash
docker compose -p pdrd-atomic-batch-test -f ops/compose.knowledge-test.yaml up --build --abort-on-container-exit --exit-code-from knowledge-test-runner > /tmp/pdrd-atomic-batch-postgres.log 2>&1
docker compose -p pdrd-atomic-batch-test -f ops/compose.knowledge-test.yaml down --remove-orphans
```

Если assert SQL batch не равен 20, исправить только соответствующий override в серверном `.env`
на `KNOWLEDGE_SERVICE_NORMATIVE_REQUIREMENTS__BATCH_SIZE=20`, повторить up.sh и проверку.
Не печатать `.env` целиком. CLI `--batch-size 1` ниже означает один **документ** очереди,
а SQL batch=20 задаётся окружением worker.

## Контрольный повтор: только два UUID, последовательно

Не запускать общий backfill и не увеличивать MAX_ATTEMPTS. На время сравнения не запускать другие
изменения нормативного каталога. Сначала сохранить и проверить фактический baseline:

```bash
atomic_run=$(date -u +%Y%m%dT%H%M%SZ)
atomic_before="/data/normative/atomic-reports/batch20-${atomic_run}-before.json"
atomic_after="/data/normative/atomic-reports/batch20-${atomic_run}-after.json"
docker compose exec -T knowledge-service normative-requirements backfill --all-sections --status --report "$atomic_before"
docker compose exec -T knowledge-service python - "$atomic_before" <<'PY'
import json, sys, urllib.request
from pathlib import Path
from pdrd_knowledge_service.core.settings import get_settings
p = Path(sys.argv[1]); r = json.loads(p.read_text(encoding="utf-8"))
targets = {"c418532c-6b29-4fa5-9554-599af833a020", "7e2c193d-2b42-4f3e-b3fe-18fcc712db8c"}
rows = {d["document_id"]: d for d in r["documents"]}
assert len(rows) == 285 and r["states"] == {"ready": 283, "failed": 2}, r["states"]
assert {k for k,d in rows.items() if d["atomic_state"] == "failed"} == targets
assert all(rows[k]["retry_exhausted"] for k in targets)
assert r["in_flight"] == 0, "Дождитесь текущих заданий перед контрольным сравнением"
for k,d in rows.items():
    if k not in targets:
        assert d["integrity"] == "verified" and d["requirements"] == d["actual_requirements"] > 0
        assert d["requirements_digest"] == d["verified_requirements_digest"]
s = get_settings(); base = s.qdrant.base_url.rstrip("/"); alias = s.qdrant.normative_collection
def read(path):
    return json.load(urllib.request.urlopen(base+path, timeout=30))["result"]
aliases = read("/aliases")["aliases"]
physical = next((a["collection_name"] for a in aliases if a["alias_name"] == alias), alias)
r["acceptance_qdrant"] = {"alias": alias, "physical": physical,
                           "points_count": read("/collections/"+alias)["points_count"]}
p.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
print("Baseline проверен: 283 ready, два exhausted failed; digest и Qdrant snapshot сохранены.")
PY
```

Если preflight не прошёл, остановиться: имеющийся третий not_indexed не включён в эту поставку.
После успешного baseline выполнить следующий блок. Подоболочка останавливается при первой ошибке;
второй документ не запускается после отказа первого.

```bash
(
set -e
docker compose exec -T knowledge-indexer python -c 'from pdrd_knowledge_service.core.settings import get_settings; assert get_settings().normative_requirements.batch_size==20'
docker compose exec -T knowledge-service normative-requirements backfill --document-id c418532c-6b29-4fa5-9554-599af833a020 --restart-exhausted --max-in-flight 1 --batch-size 1 --wait --report "/data/normative/atomic-reports/batch20-${atomic_run}-land.json"
docker compose exec -T knowledge-service normative-requirements backfill --document-id 7e2c193d-2b42-4f3e-b3fe-18fcc712db8c --restart-exhausted --max-in-flight 1 --batch-size 1 --wait --report "/data/normative/atomic-reports/batch20-${atomic_run}-urban.json"
docker compose exec -T knowledge-service normative-requirements backfill --all-sections --status --report "$atomic_after"
)
```

При отказе сначала читать индивидуальный report и `docker compose logs --tail=200 knowledge-indexer`.
Не добавлять `--retry-failed`, не повышать MAX_ATTEMPTS и не повторять restart без диагностики.
Нужны время обрыва, generation/offset/rows/json_bytes/attempt, PostgreSQL/worker логи,
состояние процессов и ресурсов. Успех малых порций не доказывает первоначальную причину.
При прерывании ожидания уже queued/building поколение продолжается обычным `--document-id ... --wait`
без повторного `--restart-exhausted`; история попыток остаётся в БД.

## Приёмка после двух успешных запусков

```bash
docker compose exec -T knowledge-service python - "$atomic_before" "$atomic_after" <<'PY'
import json, sys, urllib.request
from pathlib import Path
from pdrd_knowledge_service.core.settings import get_settings
before, after = [json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[1:]]
targets = {"c418532c-6b29-4fa5-9554-599af833a020", "7e2c193d-2b42-4f3e-b3fe-18fcc712db8c"}
old = {d["document_id"]: d for d in before["documents"]}
new = {d["document_id"]: d for d in after["documents"]}
assert set(new) == set(old) and after["states"] == {"ready": 285}, after["states"]
for k,d in new.items():
    assert d["integrity"] == "verified" and d["requirements"] == d["actual_requirements"] > 0, k
    assert d["requirements_digest"] == d["verified_requirements_digest"], k
    if k not in targets:
        for field in ("generation_id", "active_generation_id", "source_sha256", "requirements", "requirements_digest"):
            assert d[field] == old[k][field], (k,field)
s = get_settings(); base = s.qdrant.base_url.rstrip("/"); alias = s.qdrant.normative_collection
def read(path):
    return json.load(urllib.request.urlopen(base+path, timeout=30))["result"]
aliases = read("/aliases")["aliases"]
physical = next((a["collection_name"] for a in aliases if a["alias_name"] == alias), alias)
snapshot = {"alias": alias, "physical": physical, "points_count": read("/collections/"+alias)["points_count"]}
assert snapshot == before["acceptance_qdrant"], "Qdrant изменился; выясните независимую причину"
print(json.dumps({"ready": 285, "preserved_ready": 283, "digest_verified": True,
                  "qdrant_unchanged": True, "targets": [{k:new[i][k] for k in
                  ("document_id","generation_id","actual_requirements","verified_requirements_digest")}
                  for i in sorted(targets)]}, ensure_ascii=False, indent=2))
PY
```

Сверить также ссылки `administrative_restart` в двух индивидуальных отчётах с прежними generation_id.
Успех последнего блока вместе с журналом запусков подтверждает приёмку; до его выполнения
нельзя писать, что промышленная база уже имеет 285/285.

Ручная UI-проверка: под admin у двух документов в ПЗУ состояние ready и непустое количество атомов;
нормативный поиск прежних документов продолжает работать. Геометрия/сохранение Review,
авторизация и фронтенд этой поставкой не менялись.
