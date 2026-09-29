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
  assert.equal(row.children[0].children.length, 2);
  row.children[0].children[1].click();
  assert.equal(ui.get("image-dialog").open, true);
  assert.equal(ui.get("large-image").src, "/crop/example-1/1");
  assert.equal(ui.get("image-original").href, "/crop/example-1/1");
  row.children[5].children[0].click();
  assert.equal(ui.get("edit-dialog").open, true);
  assert.match(ui.get("source-text").textContent, /Исходный текст VLM/);
  ui.fields.text.value = "Правка каталога";
  await ui.get("edit-form").emit("submit");
  assert.equal(writes.length, 1);
  assert.equal(writes[0][1], 0);
  assert.equal(writes[0][2].text, "Правка каталога");
  assert.equal(ui.get("edit-dialog").open, false);
  await row.children[5].children[1].click();
  assert.equal(ui.get("history-dialog").open, true);
  assert.equal(ui.get("history-content").children.length, 1);
  await ui.get("export").click();
  assert.equal(downloads[0].filename, "experience.zip");
});

test("CAS-конфликт сохраняет правки и требует явного сброса на серверную редакцию", async () => {
  const ui = setup(); const api = apiStub();
  api.update = async () => { throw { status: 409, detail: "Редакция изменена" }; };
  await mountExperienceCatalog({ api });
  ui.get("rows").children[0].children[5].children[0].click();
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
  const request = ui.get("rows").children[0].children[5].children[1].click();
  ui.get("history-close").click();
  pending.resolve({ events: [] }); await request;
  assert.equal(ui.get("history-dialog").open, false);
});

test("новая правка неоднозначного отказа сбрасывает оценку прежнего revised текста", async () => {
  const ui = setup(); const api = apiStub();
  api.list = async () => ({ items: [record({ negative_target: "both", rejection_reason: "Неверная интерпретация" })], total: 1 });
  await mountExperienceCatalog({ api });
  ui.get("rows").children[0].children[5].children[0].click();
  ui.fields.text.value = "Изменённая формулировка"; ui.fields.text.emit("input");
  assert.equal(ui.fields.negative_target.value, "");
  assert.equal(ui.fields.rejection_reason.value, "");
  assert.match(ui.get("edit-error").textContent, /заново/);
});
