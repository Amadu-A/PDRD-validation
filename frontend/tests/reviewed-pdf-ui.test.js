// frontend/tests/reviewed-pdf-ui.test.js

/** DOM-контракт итогового PDF: понятная блокировка, повторные клики и поздний ответ старого отчёта. */

import assert from "node:assert/strict";
import test from "node:test";
import { mountReviewedPdf } from "../src/js/features/review/pdf-controls.js";
import { mountAreaConfirmations } from "../src/js/features/review/area-controls.js";
import { createReviewSync } from "../src/js/features/review/sync.js";
import { FakeElement } from "./helpers/fake-dom.js";

function fixture(t, { approved = false, pendingCount = 0 } = {}) {
  const previous = globalThis.document;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  t.after(() => { globalThis.document = previous; });
  const button = new FakeElement("button");
  const root = new FakeElement();
  root.selectors.set("[data-analysis-pdf-reviewed]", button);
  button.after = (node) => { button.description = node; };
  let session = { revision: 5, approved_revision: approved ? 5 : null, pending_count: pendingCount };
  const calls = [];
  const downloads = [];
  const busy = [];
  const captures = [];
  let capture = async () => ({ eligible: 2, excluded: 1, created: 0 });
  let pdf = async () => ({ filename: "Итоговый.pdf" });
  const controls = mountReviewedPdf({ root, jobId: "job:1",
    sync: { get session() { return session; }, run: async (command) => {
      calls.push(command);
      session = { ...session, revision: 6, approved_revision: 6,
        experience_capture: { status: "saved", eligible: 2, excluded: 1, created: 2 } };
      return session;
    } },
    api: { pdf: async (job, revision) => { calls.push([job, revision]); return pdf(); } },
    experienceApi: { capture: async (job, revision) => { captures.push([job, revision]); return capture(); } },
    download: (file) => downloads.push(file), onBusy: (value) => busy.push(value),
  });
  controls.update({ mode: "saved", session, pending: 0 });
  return { controls, button, calls, downloads, busy, captures,
    status(patch = {}) { controls.update({ mode: "saved", session, pending: 0, ...patch }); },
    pdf(callback) { pdf = callback; },
    capture(callback) { capture = callback; },
    async click() { for (const handler of button.listeners.get("click") ?? []) await handler(); },
  };
}

test("ожидающие решения блокируют PDF и пояснение доступно рядом с кнопкой", async (t) => {
  const f = fixture(t, { pendingCount: 1 });
  assert.equal(f.button.disabled, true);
  assert.match(f.button.title, /примите или отклоните/);
  assert.match(f.button.description.textContent, /примите или отклоните/);
  assert.equal(f.button.description.getAttribute("role"), "status");
  await f.click();
  assert.equal(f.calls.length, 0);
});

test("все решения разрешают утверждение даже без подтверждённых областей", async (t) => {
  const f = fixture(t);
  assert.equal(f.button.disabled, false);
  assert.match(f.button.description.textContent, /без подтверждённой области/);
  await f.click();
  assert.deepEqual(f.calls, [{ action: "approve" }, ["job:1", 6]]);
  assert.equal(f.downloads.length, 1);
  assert.equal(f.captures.length, 0);
  assert.match(f.button.description.textContent, /базу опыта.*2/);
});

test("повторный клик во время формирования не создаёт второй запрос", async (t) => {
  const f = fixture(t, { approved: true });
  let finish;
  f.pdf(() => new Promise((resolve) => { finish = resolve; }));
  const generating = f.click();
  await new Promise(setImmediate);
  await f.click();
  assert.equal(f.button.disabled, true);
  assert.deepEqual(f.calls, [["job:1", 5]]);
  finish({ filename: "Итоговый.pdf" });
  await generating;
  assert.equal(f.button.disabled, false);
  assert.equal(f.downloads.length, 1);
  assert.deepEqual(f.busy, [true, false]);
});

test("ошибка PDF сохраняет утверждённую редакцию и видна без загрузки файла", async (t) => {
  const f = fixture(t);
  f.pdf(async () => { throw Object.assign(new Error("Conflict"), { detail: "Review изменён после утверждения" }); });
  await f.click();
  assert.equal(f.downloads.length, 0);
  assert.match(f.button.description.textContent, /изменён после утверждения/);
  f.pdf(async () => ({ filename: "Повтор.pdf" }));
  await f.click();
  assert.equal(f.calls.filter((row) => row.action === "approve").length, 1);
  assert.equal(f.downloads.length, 1);
});

test("поздний ответ старого отчёта не скачивается и подписка снимается", async (t) => {
  const f = fixture(t, { approved: true });
  let finish;
  f.pdf(() => new Promise((resolve) => { finish = resolve; }));
  const generating = f.click();
  await new Promise(setImmediate);
  f.controls.dispose();
  finish({ filename: "Устаревший.pdf" });
  await generating;
  assert.equal(f.downloads.length, 0);
  assert.equal((f.button.listeners.get("click") ?? []).length, 0);
});

test("проверка области и PDF используют общую блокировку без параллельных запросов", async (t) => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  globalThis.window = { prompt: () => null };
  t.after(() => {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  });
  const root = new FakeElement();
  const button = new FakeElement("button");
  root.selectors.set("[data-analysis-pdf-reviewed]", button);
  button.after = (node) => { button.description = node; };
  const preview = new FakeElement();
  preview.dataset.reviewPreview = "";
  preview.after = (node) => root.append(node);
  root.append(preview);
  const item = new FakeElement("article");
  item.className = "analysis-result__finding";
  item.dataset.findingId = "vlm:1";
  root.append(item);
  const box = { x_min: 10, y_min: 20, x_max: 200, y_max: 100 };
  const session = { revision: 5, approved_revision: 5, pending_count: 0, area_confirmations: [],
    findings: [{ finding_id: "vlm:1", origin: "vlm", proposed_regions: [{ bbox: box }] }] };
  const requests = [];
  let finishArea;
  let finishPdf;
  let areas;
  let pdf;
  const apply = (busy = false) => {
    const status = { mode: "saved", session, pending: 0, busy };
    areas?.update(status);
    pdf?.update(status);
  };
  const sync = { get session() { return session; }, run: (command) => {
    requests.push(command.action);
    return new Promise((resolve) => { finishArea = resolve; });
  } };
  pdf = mountReviewedPdf({ root, jobId: "job:1", sync, onBusy: apply,
    experienceApi: { capture: async () => ({ eligible: 1, excluded: 0, created: 0 }) },
    api: { pdf: () => { requests.push("pdf"); return new Promise((resolve) => { finishPdf = resolve; }); } }, download: () => {},
  });
  areas = mountAreaConfirmations({ root, sync, onBusy: apply,
    snapshot: () => [{ finding_id: "vlm:1", regions: [box] }],
  });
  apply();
  const areaButton = item.children[0];
  const click = async (node) => { for (const handler of node.listeners.get("click") ?? []) await handler(); };
  const confirming = click(areaButton);
  assert.equal(button.disabled, true);
  await click(button);
  assert.deepEqual(requests, ["confirm_area"]);
  finishArea(session);
  await confirming;
  const generating = click(button);
  await new Promise(setImmediate);
  assert.equal(areaButton.disabled, true);
  await click(areaButton);
  assert.deepEqual(requests, ["confirm_area", "pdf"]);
  finishPdf({ filename: "Итоговый.pdf" });
  await generating;
  assert.equal(areaButton.disabled, false);
  assert.equal(button.disabled, false);
  areas.dispose();
  pdf.dispose();
});

test("после последнего решения PDF активен, rejected и Gold не требуют дополнительных действий", async (t) => {
  const f = fixture(t, { pendingCount: 2 });
  f.status({ session: { revision: 5, pending_count: 2 }, mode: "saving", pending: 1 });
  assert.equal(f.button.disabled, true);
  assert.match(f.button.title, /примите или отклоните.*2/);
  f.status({ session: { revision: 5, pending_count: 0,
    findings: [{ decision: "accepted" }, { decision: "rejected" }, { origin: "manual", decision: "accepted" }] } });
  assert.equal(f.button.disabled, false);
  f.status({ mode: "saving", pending: 1 });
  assert.equal(f.button.disabled, true);
});

test("ошибка Experience не мешает PDF, повторное скачивание завершает перенос", async (t) => {
  const f = fixture(t, { approved: true });
  f.capture(async () => { throw { detail: "PNG временно недоступен" }; });
  await f.click();
  assert.equal(f.downloads.length, 1);
  assert.match(f.button.description.textContent, /PNG временно недоступен.*Повторите скачивание/);
  assert.equal(f.button.disabled, false);
  f.capture(async () => ({ eligible: 2, excluded: 1, created: 2 }));
  await f.click();
  assert.equal(f.downloads.length, 2);
  assert.deepEqual(f.captures, [["job:1", 5], ["job:1", 5]]);
  assert.match(f.button.description.textContent, /базу опыта.*2/);
  assert.equal(f.calls.filter((row) => row.action === "approve").length, 0);
});

test("при смене отчёта во время переноса Experience PDF старого отчёта не запрашивается", async (t) => {
  const f = fixture(t, { approved: true });
  let finish;
  f.capture(() => new Promise((resolve) => { finish = resolve; }));
  const saving = f.click();
  f.controls.dispose();
  finish({ eligible: 2, excluded: 0, created: 2 });
  await saving;
  assert.equal(f.calls.length, 0);
  assert.equal(f.downloads.length, 0);
});

test("настоящая очередь Review включает PDF после сохранения Accepted/Rejected/Gold", async (t) => {
  const previous = globalThis.document;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  t.after(() => { globalThis.document = previous; });
  const jobId = "job:flow";
  const button = new FakeElement("button");
  const root = new FakeElement();
  root.selectors.set("[data-analysis-pdf-reviewed]", button);
  button.after = (node) => { button.description = node; };
  const rows = ["vlm:1", "vlm:2", "manual:gold"].map((findingId) => ({
    finding_id: findingId, origin: findingId.startsWith("manual:") ? "manual" : "vlm",
    decision: "pending", text: "Полный текст", normative_basis: "", regions: [], callout_box: null,
  }));
  const decisions = new Map(rows.map((row) => [row.finding_id, "pending"]));
  let revision = 0;
  let approvedRevision = null;
  const requests = [];
  const downloads = [];
  const session = () => ({ job_id: jobId, revision, approved_revision: approvedRevision,
    pending_count: [...decisions.values()].filter((value) => value === "pending").length });
  const api = {
    config: async () => ({ enabled: true }), open: async () => session(),
    command: async (_jobId, body) => {
      assert.equal(body.expected_revision, revision);
      requests.push(body.action);
      revision += 1;
      if (body.action === "decide") decisions.set(body.finding_id, body.decision);
      else approvedRevision = revision;
      return { ...session(), ...(body.action === "approve"
        ? { experience_capture: { status: "saved", eligible: 2, excluded: 1 } } : {}) };
    },
    pdf: async (job, expectedRevision) => {
      requests.push("pdf");
      assert.equal(job, jobId);
      assert.equal(expectedRevision, approvedRevision);
      return { filename: "Reviewed.pdf" };
    },
  };
  let controls;
  const sync = createReviewSync({ jobId, api, snapshot: () => structuredClone(rows), hydrate() {},
    onStatus: (status) => controls?.update(status),
  });
  controls = mountReviewedPdf({ root, jobId, api, sync,
    experienceApi: { capture: () => { assert.fail("approve уже сохранил Experience"); } },
    download: (file) => downloads.push(file),
  });
  t.after(() => controls.dispose());
  await sync.start();
  assert.equal(button.disabled, true);
  assert.match(button.title, /Ожидают решения: 3/);
  for (const [index, decision] of ["accepted", "rejected", "accepted"].entries()) {
    rows[index].decision = decision;
    sync.changed();
  }
  await sync.settled();
  assert.equal(button.disabled, false);
  for (const handler of button.listeners.get("click")) await handler();
  assert.deepEqual(requests, ["decide", "decide", "decide", "approve", "pdf"]);
  assert.equal(downloads.length, 1);
  assert.match(button.description.textContent, /базу опыта.*2/);
});
