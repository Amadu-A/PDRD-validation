// frontend/tests/experience-server.test.js

/** Серверный каталог: фильтры/страницы, миниатюры, CAS, история и поздние ответы. */
import assert from "node:assert/strict";
import test from "node:test";
import { mountExperienceCatalog } from "../src/js/features/experience/server-page.js";
import { deferred, record, setup } from "./experience-dom.js";

function apiStub() {
  return { list: async () => ({ items: [record()], total: 1 }), imageUrl: (id, index) => `/crop/${id}/${index}`,
    update: async () => record({ revision: 1 }), get: async () => record({ revision: 2 }),
    history: async () => ({ events: [{ revision: 0, actor: "engineer:1", occurred_at: "2026-09-28T10:00:00Z", snapshot: { text: "Исходная редакция", normative_basis: "СП 1" } }] }),
    versions: async () => ({ items: [], applied: [] }), deleteSelection: async () => ({ deleted: 1 }),
    export: async () => ({ blob: new Blob(["PK"]), filename: "experience.zip" }) };
}

test("серверные строки, все области, редактирование и история доступны без демо", async () => {
  const ui = setup(); const api = apiStub(); const writes = [];
  api.update = async (...args) => { writes.push(args); return record({ revision: 1 }); };
  const downloads = [];
  await mountExperienceCatalog({ api, download: (file) => downloads.push(file) });
  assert.equal(ui.get("rows").children.length, 1);
  assert.match(ui.get("caption").textContent, /Сохранённые/);
  const row = ui.get("rows").children[0];
  assert.equal(row.children[1].children.length, 2);
  row.children[1].children[1].click();
  assert.equal(ui.get("image-dialog").open, true);
  assert.equal(ui.get("large-image").src, "/crop/example-1/1");
  assert.equal(ui.get("image-original").href, "/crop/example-1/1");
  row.children[9].children[0].click();
  assert.equal(ui.get("edit-dialog").open, true);
  assert.match(ui.get("source-text").textContent, /Исходный текст VLM/);
  ui.fields.text.value = "Правка каталога";
  await ui.get("edit-form").emit("submit");
  assert.equal(writes.length, 1);
  assert.equal(writes[0][1], 0);
  assert.equal(writes[0][2].text, "Правка каталога");
  assert.equal(ui.get("edit-dialog").open, false);
  await row.children[9].children[1].click();
  assert.equal(ui.get("history-dialog").open, true);
  assert.equal(ui.get("history-content").children.length, 1);
  await ui.get("export").click();
  assert.equal(downloads[0].filename, "experience.zip");
});

test("CAS-конфликт сохраняет правки и требует явного сброса на серверную редакцию", async () => {
  const ui = setup(); const api = apiStub();
  api.update = async () => { throw { status: 409, detail: "Редакция изменена" }; };
  await mountExperienceCatalog({ api });
  ui.get("rows").children[0].children[9].children[0].click();
  ui.fields.text.value = "Несохранённый текст";
  await ui.get("edit-form").emit("submit");
  assert.equal(ui.get("edit-dialog").open, true);
  assert.equal(ui.fields.text.value, "Несохранённый текст");
  assert.equal(ui.get("edit-reload").hidden, false);
  await ui.get("edit-reload").click();
  assert.equal(ui.fields.text.value, "Полный исправленный текст");
  assert.equal(ui.get("edit-reload").hidden, true);
});

test("фильтры применяются сервером и переход между страницами меняет offset", async () => {
  const ui = setup(); const api = apiStub(); const queries = [];
  api.list = async (criteria) => { queries.push(criteria); return { items: [record()], total: 80 }; };
  await mountExperienceCatalog({ api });
  ui.get("next").click();
  await new Promise((done) => setImmediate(done));
  assert.equal(queries.at(-1).offset, 50);
  ui.filter.tag.value = "edited:rejected";
  ui.get("filter").emit("change");
  await new Promise((done) => setImmediate(done));
  assert.equal(queries.at(-1).offset, 0);
  assert.equal(queries.at(-1).tag, "edited:rejected");
  assert.equal(queries.at(-1).limit, 50);
});

test("поздний ответ каталога не заменяет новую фильтрацию", async () => {
  const ui = setup(); const api = apiStub(); const older = deferred(); const newer = deferred();
  let calls = 0;
  api.list = async () => (++calls === 1 ? { items: [], total: 0 } : calls === 2 ? older.promise : newer.promise);
  const controller = await mountExperienceCatalog({ api });
  const first = controller.load(); const second = controller.load();
  newer.resolve({ items: [record({ id: "new-result" })], total: 1 }); await second;
  older.resolve({ items: [record({ id: "old-result" })], total: 1 }); await first;
  assert.equal(ui.get("rows").children[0].dataset.experienceId, "new-result");
});

test("ошибка каталога не подменяется демо и не разрешает выгрузку", async () => {
  const ui = setup(); const api = apiStub();
  api.list = async () => { throw { detail: "Сервис недоступен" }; };
  await mountExperienceCatalog({ api });
  assert.equal(ui.get("rows").children.length, 0);
  assert.match(ui.get("notice").textContent, /Сервис недоступен/);
  assert.equal(ui.get("export").disabled, true);
});

test("поздняя история не открывается снова после закрытия модалки", async () => {
  const ui = setup(); const api = apiStub(); const pending = deferred(); api.history = () => pending.promise;
  await mountExperienceCatalog({ api });
  const request = ui.get("rows").children[0].children[9].children[1].click();
  ui.get("history-close").click();
  pending.resolve({ events: [] }); await request;
  assert.equal(ui.get("history-dialog").open, false);
});

test("новая правка неоднозначного отказа сбрасывает оценку прежнего revised текста", async () => {
  const ui = setup(); const api = apiStub();
  api.list = async () => ({ items: [record({ negative_target: "both", rejection_reason: "Неверная интерпретация" })], total: 1 });
  await mountExperienceCatalog({ api });
  ui.get("rows").children[0].children[9].children[0].click();
  ui.fields.text.value = "Изменённая формулировка"; ui.fields.text.emit("input");
  assert.equal(ui.fields.negative_target.value, "");
  assert.equal(ui.fields.rejection_reason.value, "");
  assert.match(ui.get("edit-error").textContent, /заново/);
});

test("выбор всех проходит страницы; удаление отправляет один запрос с ревизиями", async () => {
  const ui = setup(); const api = apiStub(); const writes = []; let removed = false;
  const records = Array.from({ length: 120 }, (_, index) => record({ id: `example-${index}`, revision: index }));
  api.list = async ({ offset = 0, limit = 50 }) => ({ items: removed ? [] : records.slice(offset, offset + limit), total: removed ? 0 : 120 });
  api.deleteSelection = async (items) => { writes.push(items); removed = true; return { deleted: items.length }; };
  const controller = await mountExperienceCatalog({ api });
  await ui.get("select-all").click();
  assert.equal(controller.selection.references().length, 120);
  assert.equal(ui.get("rows").children[0].children[0].children[0].checked, true);
  assert.equal(ui.get("delete-selection").disabled, false);
  await ui.get("delete-selection").click();
  assert.equal(writes.length, 1); assert.equal(writes[0].length, 120);
  assert.deepEqual(writes[0][119], { id: "example-119", revision: 119 });
  assert.equal(ui.get("rows").children.length, 0);
  assert.equal(controller.selection.references().length, 0);
});

test("конфликт удаления сохраняет выбор; очистка снимает чекбоксы без потери узла", async () => {
  const ui = setup(); const api = apiStub();
  api.deleteSelection = async () => { throw { detail: "Редакция изменилась", status: 409 }; };
  const controller = await mountExperienceCatalog({ api });
  const checkbox = ui.get("rows").children[0].children[0].children[0];
  checkbox.checked = true; checkbox.emit("change");
  await ui.get("delete-selection").click();
  assert.equal(controller.selection.references().length, 1);
  assert.match(ui.get("notice").textContent, /изменилась/);
  ui.get("clear-selection").click();
  assert.equal(checkbox.checked, false);
  assert.equal(ui.get("rows").children[0].children[0].children[0], checkbox);
});

test("просмотр версии подсвечивает состав и выбирает отсутствующие редакции без применения", async () => {
  const ui = setup(); const api = apiStub(); const applies = [];
  const rows = [record({ id: "included", revision: 1 }), record({ id: "updated", revision: 2 }), record({ id: "missing" })];
  const version = { id: "v1", kind: "vector", name: "Первая", model: "shared-embedding", status: "ready",
    section_id: "СП 1:6", section_title: "Раздел 6", created_at: "2026-09-29T10:00:00Z", revision: 0,
    quality_approved: true, members: [{ example_id: "included", example_revision: 1 }, { example_id: "updated", example_revision: 1 }] };
  api.list = async () => ({ items: rows, total: rows.length });
  api.versions = async () => ({ items: [version], applied: [] }); api.version = async () => version;
  api.applyVersion = async (...args) => { applies.push(args); };
  const controller = await mountExperienceCatalog({ api });
  ui.get("version").value = "v1"; await ui.get("version").emit("change");
  assert.equal(ui.filter.section_id.value, "СП 1:6");
  assert.match(ui.get("rows").children[0].className, /member/);
  assert.match(ui.get("rows").children[1].children[5].children.at(-1).textContent, /редакция 1/);
  await ui.get("select-missing").click();
  assert.deepEqual(controller.selection.references().map((item) => item.id), ["updated", "missing"]);
  assert.equal(applies.length, 0);
  ui.get("version-kind").value = "fine_tune"; await ui.get("version-kind").emit("change");
  await new Promise((done) => setImmediate(done));
  assert.equal(controller.selection.memberRevision("included"), null);
});

test("новая версия отправляет только выбранные редакции, модель и название", async () => {
  const ui = setup(); const api = apiStub(); const writes = [];
  const version = { id: "dataset", kind: "fine_tune", name: "Набор", model: "shared-vlm", status: "prepared",
    section_id: "СП 1:6", section_title: "Раздел 6", members: [], created_at: "2026-09-29T10:00:00Z", revision: 0 };
  api.createVersion = async (fields) => { writes.push(fields); return version; };
  api.versions = async () => ({ items: writes.length ? [version] : [], applied: [] }); api.version = async () => version;
  await mountExperienceCatalog({ api });
  ui.get("version-kind").value = "fine_tune"; await ui.get("version-kind").emit("change");
  await new Promise((done) => setImmediate(done));
  const checkbox = ui.get("rows").children[0].children[0].children[0]; checkbox.checked = true; checkbox.emit("change");
  ui.get("build-version").click(); ui.versionFields.name.value = "Набор";
  await ui.get("version-form").emit("submit");
  assert.deepEqual(writes, [{ kind: "fine_tune", name: "Набор", model: "shared-vlm", items: [{ id: "example-1", revision: 0 }] }]);
});

test("удаление последней строки страницы возвращает на существующую страницу", async () => {
  const ui = setup(); const api = apiStub();
  const records = Array.from({ length: 51 }, (_, index) => record({ id: `example-${index}` }));
  api.list = async ({ offset = 0, limit = 50 }) => ({ items: records.slice(offset, offset + limit), total: records.length });
  api.deleteSelection = async () => { records.pop(); return { deleted: 1 }; };
  await mountExperienceCatalog({ api }); await ui.get("next").click();
  await new Promise((done) => setImmediate(done));
  assert.equal(ui.get("rows").children.length, 1);
  await ui.get("rows").children[0].children[9].children[2].click();
  assert.equal(ui.get("rows").children.length, 50);
  assert.match(ui.get("count").textContent, /1–50 из 50/);
  assert.equal(ui.get("previous").disabled, true);
});

test("обновление после удаления версии в другой вкладке снимает старую подсветку", async () => {
  const ui = setup(); const api = apiStub(); let deleted = false;
  const version = { id: "v1", kind: "vector", name: "Версия", model: "shared-embedding", status: "ready",
    created_at: "2026-09-29T10:00:00Z", section_id: "СП 1:6", section_title: "Раздел 6",
    members: [{ example_id: "example-1", example_revision: 0 }] };
  api.versions = async () => ({ items: deleted ? [] : [version], applied: [] }); api.version = async () => version;
  const controller = await mountExperienceCatalog({ api });
  ui.get("version").value = "v1"; await ui.get("version").emit("change");
  assert.equal(controller.selection.memberRevision("example-1"), 0);
  deleted = true; await ui.get("refresh").click();
  assert.equal(controller.selection.memberRevision("example-1"), null);
  assert.equal(ui.filter.section_id.value, "");
  assert.doesNotMatch(ui.get("rows").children[0].className, /member/);
});
