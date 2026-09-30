// frontend/tests/reviewed-pdf.test.js

/** Утверждение, очередь, блокировка PDF и проверка ответа сервера. */

import assert from "node:assert/strict";
import test from "node:test";
import { fetchPdf, ApiError } from "../src/js/api.js";
import { requiresAreaAcceptance, reviewedPdfAvailability } from "../src/js/features/review/pdf-model.js";
import { createReviewSync } from "../src/js/features/review/sync.js";
import { mountReviewedPdf } from "../src/js/features/review/pdf-controls.js";

const JOB = "00000000-0000-4000-8000-000000000001";

test("старое принятое Review подтверждает текущую область при PDF, Bad не получает новую", () => {
  const located = { finding_id: "vlm:1", origin: "vlm", decision: "accepted", proposed_regions: [{}] };
  assert.equal(requiresAreaAcceptance({ findings: [located] }), true);
  assert.equal(requiresAreaAcceptance({ findings: [located], area_confirmations: [{ finding_id: "vlm:1", valid: true }] }), false);
  assert.equal(requiresAreaAcceptance({ findings: [{ ...located, decision: "rejected" }] }), false);
  assert.equal(requiresAreaAcceptance({ findings: [{ ...located, proposed_regions: [] }] }), false);
  assert.equal(requiresAreaAcceptance({ findings: [{ ...located, origin: "manual" }] }), false);
});

test("PDF требует сохранённых решений всех находок, в том числе Gold", () => {
  const session = { revision: 5, pending_count: 0 };
  assert.equal(reviewedPdfAvailability({ mode: "saved", session }).enabled, true);
  for (const patch of [{ mode: "local" }, { mode: "saving" }, { pending: 1 }, { busy: true }, { session: { pending_count: 1 } }, { mode: "conflict" }]) {
    assert.equal(reviewedPdfAvailability({ mode: "saved", session, ...patch }).enabled, false);
  }
});

test("утверждение ждёт очереди правок и использует её последнюю ревизию", async () => {
  let state = [{ finding_id: "vlm:1", origin: "vlm", text: "Исходный текст", normative_basis: "", decision: "pending", regions: [], callout_box: null }];
  const bodies = [];
  let finish;
  const sync = createReviewSync({ jobId: JOB, snapshot: () => structuredClone(state), hydrate: () => {},
    api: { config: async () => ({ enabled: true }), open: async () => ({ job_id: JOB, revision: 0 }),
      command: async (_id, body) => {
        bodies.push(body);
        if (body.action === "decide") await new Promise((resolve) => { finish = resolve; });
        return { job_id: JOB, revision: body.expected_revision + 1, approved_revision: body.action === "approve" ? body.expected_revision + 1 : null };
      } },
  });
  await sync.start();
  state[0].decision = "accepted";
  sync.changed();
  const approval = sync.run({ action: "approve" });
  assert.equal(bodies.length, 1);
  finish();
  const result = await approval;
  assert.deepEqual(bodies.map((body) => [body.action, body.expected_revision]), [["decide", 0], ["approve", 1]]);
  assert.equal(result.approved_revision, 2);
});

test("сбой подтверждения не разрешает итоговый PDF и не повторяется сам", async () => {
  let calls = 0;
  const sync = createReviewSync({ jobId: JOB, snapshot: () => [], hydrate: () => {},
    api: { config: async () => ({ enabled: true }), open: async () => ({ job_id: JOB, revision: 0 }),
      command: async () => { calls += 1; throw Object.assign(new Error("Конфликт"), { status: 409 }); } },
  });
  await sync.start();
  await assert.rejects(sync.run({ action: "approve" }), /не подтверждена/);
  await assert.rejects(sync.run({ action: "approve" }), /восстановите/);
  assert.equal(calls, 1);
});

test("HTTP ошибки и HTML вместо PDF не скачиваются как файл", async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Review изменён" }), { status: 409 });
    await assert.rejects(fetchPdf("/reviewed-pdf"), (error) => error instanceof ApiError && error.status === 409 && error.detail === "Review изменён");
    globalThis.fetch = async () => new Response("<html>Ошибка</html>", { headers: { "Content-Type": "application/pdf" } });
    await assert.rejects(fetchPdf("/reviewed-pdf"), /не вернул/);
    globalThis.fetch = async () => new Response("%PDF-1.7\nexample", { headers: { "Content-Type": "application/pdf", "Content-Disposition": "attachment; filename*=UTF-8''%D0%9F%D0%BB%D0%B0%D0%BD_reviewed.pdf" } });
    const file = await fetchPdf("/reviewed-pdf");
    assert.equal(file.filename, "План_reviewed.pdf");
    assert.equal(await file.blob.slice(0, 5).text(), "%PDF-");
  } finally { globalThis.fetch = previous; }
});

class Element {
  constructor() { this.listeners = new Map(); this.disabled = true; this.textContent = ""; }
  setAttribute() {}
  after(node) { this.next = node; }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  removeEventListener(name) { this.listeners.delete(name); }
}

test("общая разблокировка не стирает результат скачивания или серверную ошибку", async () => {
  const previous = globalThis.document;
  const button = new Element();
  globalThis.document = { createElement: () => new Element() };
  const session = { revision: 6, approved_revision: 6, pending_count: 0 };
  const status = { mode: "saved", session, pending: 0 };
  let fail = false;
  let controls;
  try {
    controls = mountReviewedPdf({ root: { querySelector: () => button }, jobId: JOB,
      experienceApi: { capture: async () => ({ eligible: 1, excluded: 0, created: 0 }) },
      sync: { session }, download() {},
      api: { pdf: async () => {
        if (fail) throw new ApiError(409, "Review изменён во время формирования");
        return { filename: "Итог.pdf" };
      } },
      onBusy: () => controls?.update(status),
    });
    controls.update(status);
    await button.listeners.get("click")();
    assert.match(button.next.textContent, /сформирован из утверждённой редакции/);
    fail = true;
    await button.listeners.get("click")();
    assert.match(button.next.textContent, /изменён во время формирования/);
  } finally { controls?.dispose(); globalThis.document = previous; }
});

test("кнопка утверждает ровно один раз, скачивает утверждённую редакцию и показывает ошибку", async () => {
  const previous = globalThis.document;
  const button = new Element();
  globalThis.document = { createElement: () => new Element() };
  let session = { job_id: JOB, revision: 5, pending_count: 0, approved_revision: null };
  const calls = [];
  const busy = [];
  try {
    const controls = mountReviewedPdf({ root: { querySelector: () => button }, jobId: JOB,
      experienceApi: { capture: async () => ({ eligible: 1, excluded: 0, created: 0 }) },
      sync: { get session() { return session; }, run: async (body) => { calls.push(body); session = { ...session, revision: 6, approved_revision: 6 }; return session; } },
      api: { pdf: async (job, revision) => { calls.push([job, revision]); return { filename: "План.pdf" }; } },
      download: (file) => calls.push(file.filename), onBusy: (value) => busy.push(value),
    });
    controls.update({ mode: "saved", session, pending: 0 });
    await button.listeners.get("click")();
    controls.update({ mode: "saved", session, pending: 0 });
    await button.listeners.get("click")();
    assert.equal(calls.filter((value) => value?.action === "approve").length, 1);
    assert.deepEqual(calls.filter(Array.isArray), [[JOB, 6], [JOB, 6]]);
    assert.deepEqual(busy, [true, false, true, false]);
    controls.dispose();
    assert.equal(button.listeners.size, 0);
  } finally { globalThis.document = previous; }
});
