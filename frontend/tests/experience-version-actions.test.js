// frontend/tests/experience-version-actions.test.js

/** Фиксированный экспорт, отчёт оценки и отзыв допуска не меняют ручной выбор каталога. */
import assert from "node:assert/strict";
import test from "node:test";
import { createExperienceApi } from "../src/js/features/experience/api.js";
import { mountExperienceCatalog } from "../src/js/features/experience/server-page.js";
import { readExperienceQualityReport } from "../src/js/features/experience/version-actions.js";
import { deferred, record, setup } from "./experience-dom.js";

/** Предоставляет отдельные ответы реестра и полной версии, повторяя серверный контракт. */
async function fixture(fields = {}) {
  const ui = setup(); const requests = []; const downloads = [];
  let version = { id: "version-1", kind: "vector", name: "Выбранная версия", model: "shared-embedding",
    status: "ready", revision: 7, quality_approved: false, quality_report: null,
    section_id: "СП 1:6", section_title: "Раздел 6", created_at: "2026-09-29T10:00:00Z",
    members: [{ example_id: "example-1", example_revision: 0 }], ...fields };
  const api = {
    list: async () => ({ items: [record()], total: 1 }), imageUrl: () => "/crop",
    versions: async () => { const { members, ...summary } = version; return { items: [{ ...summary, member_count: members.length }], applied: [] }; },
    version: async () => version,
    export: async () => { throw new Error("Не должна выгружаться текущая фильтрация каталога"); },
    exportVersion: async (id) => { requests.push(["dataset", id]); return { blob: new Blob(["PK"]), filename: "fixed.zip" }; },
    approveVersionQuality: async (id, revision, report) => {
      requests.push(["quality", id, revision, report]);
      version = { ...version, revision: revision + 1, quality_approved: report.approved, quality_report: report }; return version;
    },
    revokeVersionQuality: async (id, revision) => {
      requests.push(["revoke", id, revision]); version = { ...version, revision: revision + 1, quality_approved: false, quality_report: null }; return version;
    },
  };
  const controller = await mountExperienceCatalog({ api, download: (file) => downloads.push(file) });
  controller.selection.toggle(record(), true);
  if (version.kind !== "vector") { ui.get("version-kind").value = version.kind; await ui.get("version-kind").emit("change"); await new Promise((done) => setImmediate(done)); }
  ui.get("version").value = version.id; await ui.get("version").emit("change");
  return { ui, api, controller, requests, downloads };
}

/** Создаёт браузерный файл отчёта без использования локальной файловой системы. */
function reportFile(report) {
  const text = JSON.stringify(report);
  return { size: new TextEncoder().encode(text).byteLength, text: async () => text };
}

for (const kind of ["vector", "fine_tune"]) {
  test(`фиксированный ZIP ${kind} использует выбранную версию независимо от фильтра и чекбоксов`, async () => {
    const { ui, controller, requests, downloads } = await fixture({ kind, status: kind === "vector" ? "ready" : "prepared" });
    const selected = controller.selection.references();
    await ui.get("version-dataset").click();
    assert.deepEqual(requests, [["dataset", "version-1"]]);
    assert.equal(downloads[0].filename, "fixed.zip");
    assert.deepEqual(controller.selection.references(), selected);
    assert.equal(ui.get("version-kind").value, kind);
    assert.match(ui.get("notice").textContent, /Текущие фильтры каталога его состав не меняют/);
    if (kind === "fine_tune") { assert.equal(ui.get("quality-controls").hidden, true); assert.equal(ui.get("quality-submit").disabled, true); }
  });
}

test("JSON-отчёт отправляется с редакцией версии и не меняет чекбоксы или тип просмотра", async () => {
  const { ui, controller, requests } = await fixture();
  const selected = controller.selection.references(); const report = { approved: true, version_id: "version-1" };
  ui.get("quality-file").files = [reportFile(report)];
  await ui.get("quality-submit").click();
  assert.deepEqual(requests, [["quality", "version-1", 7, report]]);
  assert.deepEqual(controller.selection.references(), selected);
  assert.equal(ui.get("version-kind").value, "vector");
  assert.equal(controller.selection.memberRevision("example-1"), 0);
  assert.equal(ui.get("active-vector").children[1].disabled, false);
  assert.equal(ui.get("quality-revoke").disabled, false);
  assert.match(ui.get("version-quality-status").textContent, /сервер подтвердил допуск/);
  assert.match(ui.get("notice").textContent, /не включает E автоматически/);
  await ui.get("quality-revoke").click();
  assert.deepEqual(requests.at(-1), ["revoke", "version-1", 8]);
  assert.equal(ui.get("active-vector").children[1].disabled, true);
  assert.equal(ui.get("quality-revoke").disabled, true);
  assert.deepEqual(controller.selection.references(), selected);
});

test("сохранённый отрицательный отчёт не разрешает применение, но допускает отзыв", async () => {
  const { ui } = await fixture();
  ui.get("quality-file").files = [reportFile({ approved: false })];
  await ui.get("quality-submit").click();
  assert.equal(ui.get("active-vector").children[1].disabled, true);
  assert.equal(ui.get("quality-revoke").disabled, false);
  assert.match(ui.get("version-quality-status").textContent, /отчёт сохранён.*допуск отсутствует/);
});

test("загрузка JSON блокирует параллельное изменение версии до завершения чтения и CAS-записи", async () => {
  const { ui, controller, requests } = await fixture(); const pending = deferred();
  ui.get("quality-file").files = [{ size: 17, text: () => pending.promise }];
  const operation = ui.get("quality-submit").click();
  controller.selection.changed();
  assert.equal(ui.get("version-kind").disabled, true);
  assert.equal(ui.get("version-delete").disabled, true);
  assert.equal(ui.get("version-dataset").disabled, true);
  assert.equal(ui.get("prepare-fine-tune").disabled, true);
  ui.get("version-rename").click(); assert.notEqual(ui.get("version-dialog").open, true);
  await ui.get("quality-revoke").click(); assert.equal(requests.length, 0);
  pending.resolve('{"approved":true}'); await operation;
  assert.equal(requests.length, 1);
  assert.equal(ui.get("version-kind").disabled, false);
  assert.equal(ui.get("version-dataset").disabled, false);
});

test("ошибка CAS сохраняет ручной выбор, серверный статус и понятное сообщение", async () => {
  const { ui, api, controller } = await fixture(); const selected = controller.selection.references();
  api.approveVersionQuality = async () => { throw { status: 409, detail: "Редакция версии изменена; обновите реестр." }; };
  ui.get("quality-file").files = [reportFile({ approved: true })];
  await ui.get("quality-submit").click();
  assert.match(ui.get("notice").textContent, /Редакция версии изменена/);
  assert.deepEqual(controller.selection.references(), selected);
  assert.equal(ui.get("active-vector").children[1].disabled, true);
  assert.equal(ui.get("quality-submit").disabled, false);
});

test("в отсутствие JSON-файла запрос допуска не отправляется", async () => {
  const { ui, requests } = await fixture();
  await ui.get("quality-submit").click();
  assert.equal(requests.length, 0);
  assert.match(ui.get("notice").textContent, /Выберите JSON-отчёт/);
});

test("до завершения смены просмотра действия прежней версии недоступны", async () => {
  const { ui, api, requests } = await fixture(); const pending = deferred();
  api.version = () => pending.promise;
  const operation = ui.get("version").emit("change");
  assert.equal(ui.get("version-status").hidden, true);
  assert.equal(ui.get("version-dataset").disabled, true);
  assert.equal(ui.get("quality-submit").disabled, true);
  await ui.get("version-dataset").click(); assert.equal(requests.length, 0);
  pending.resolve({ id: "version-1", kind: "vector", name: "Новая редакция", model: "shared-embedding",
    revision: 8, status: "ready", quality_approved: false, section_id: "СП 1:6", section_title: "Раздел 6", members: [], created_at: "2026-09-29T10:00:00Z" });
  await operation;
  assert.equal(ui.get("version-dataset").disabled, false);
});

test("рабочие назначения нескольких разделов показываются для выбранного нормативного раздела", async () => {
  const ui = setup();
  const versions = ["A", "B"].map((section) => ({ id: `v-${section}`, kind: "vector", name: `Версия ${section}`,
    model: "shared-embedding", status: "ready", revision: 1, quality_approved: true,
    section_id: section, section_title: `Раздел ${section}`, created_at: "2026-09-29T10:00:00Z", members: [] }));
  const api = { list: async () => ({ items: [record()], total: 1 }), imageUrl: () => "/crop",
    versions: async () => ({ items: versions.map(({ members, ...item }) => ({ ...item, member_count: members.length })),
      applied: versions.map((item) => ({ kind: item.kind, section_id: item.section_id, version_id: item.id })) }),
    version: async (id) => versions.find((item) => item.id === id) };
  const controller = await mountExperienceCatalog({ api }); controller.selection.toggle(record(), true);
  const selection = controller.selection.references();
  assert.match(ui.get("active-section").textContent, /Назначено версий: 2/);
  assert.equal(ui.get("active-vector").value, "");
  ui.get("version").value = "v-B"; await ui.get("version").emit("change");
  assert.equal(ui.get("active-vector").value, "v-B");
  assert.equal(ui.get("active-vector").children.length, 2);
  assert.match(ui.get("active-section").textContent, /Раздел B/);
  assert.deepEqual(controller.selection.references(), selection);
});

test("JSON-отчёт ограничивается по размеру, структуре и действительному UTF-8 содержимому", async () => {
  for (const file of [undefined, { size: 0 }, { size: 1024 * 1024 + 1 }, reportFile([]), reportFile(null), reportFile("строка")]) {
    await assert.rejects(readExperienceQualityReport(file));
  }
  await assert.rejects(readExperienceQualityReport({ size: 2, text: async () => "{x" }), /корректный JSON/);
  await assert.rejects(readExperienceQualityReport({ size: 1, text: async () => "я".repeat(600000) }), /превышает/);
  assert.deepEqual(await readExperienceQualityReport(reportFile({ approved: false })), { approved: false });
});

test("сетевой адаптер отделяет ZIP версии от каталога и использует серверный CAS-контракт качества", async (t) => {
  const previous = globalThis.fetch; const calls = []; t.after(() => { globalThis.fetch = previous; });
  globalThis.fetch = async (url, options = {}) => {
    calls.push([url, options]);
    return url.endsWith("/dataset") ? new Response(new Blob(["PK"]), { headers: { "Content-Type": "application/zip" } }) : new Response("{}");
  };
  const api = createExperienceApi();
  assert.equal((await api.exportVersion("version/id")).filename, "experience-version.zip");
  assert.equal(calls[0][0], "/api/v1/experience-versions/version%2Fid/dataset");
  await api.approveVersionQuality("version/id", 3, { approved: false });
  assert.equal(calls[1][0], "/api/v1/experience-versions/version%2Fid/quality");
  assert.equal(calls[1][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[1][1].body), { expected_revision: 3, report: { approved: false } });
  await api.revokeVersionQuality("version/id", 4);
  assert.equal(calls[2][1].method, "DELETE");
  assert.deepEqual(JSON.parse(calls[2][1].body), { expected_revision: 4 });
  globalThis.fetch = async () => new Response("<html>", { headers: { "Content-Type": "text/html" } });
  await assert.rejects(api.exportVersion("version-1"), /неверного формата/);
  globalThis.fetch = async () => new Response('{"detail":"Нет прав на задание набора."}', { status: 403 });
  await assert.rejects(api.exportVersion("version-1"), /Нет прав на задание набора/);
});
