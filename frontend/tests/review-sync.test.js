// frontend/tests/review-sync.test.js

/** Последовательная запись Review, восстановление, CAS и потерянные ответы. */

import assert from "node:assert/strict";
import test from "node:test";
import { createReviewSync } from "../src/js/features/review/sync.js";
import { reviewCommands } from "../src/js/features/review/commands.js";
import { createReviewApi } from "../src/js/features/review/api.js";

const JOB = "00000000-0000-4000-8000-000000000001";
const entry = { finding_id: "vlm:1", origin: "vlm", text: "VLM", normative_basis: "СП 1",
  decision: "pending", regions: [], callout_box: null };
const clone = (value) => JSON.parse(JSON.stringify(value));

function fixture({ api = {} } = {}) {
  let current = [clone(entry)];
  const requests = [];
  const statuses = [];
  const sync = createReviewSync({
    jobId: JOB,
    api: {
      config: async () => ({ enabled: true }),
      open: async () => ({ job_id: JOB, revision: 10, findings: [entry] }),
      command: async (job, body) => { requests.push({ job, body }); return { job_id: job, revision: body.expected_revision + 1 }; },
      ...api,
    },
    hydrate: (session) => { current = clone(session.findings); },
    snapshot: () => clone(current),
    onStatus: (status) => statuses.push(status),
  });
  return { sync, requests, statuses, change(patch) { Object.assign(current[0], patch); sync.changed(); } };
}

test("серверный снимок загружается до редактирования; без изменений запросов нет", async () => {
  const f = fixture();
  f.change({ text: "Не должно уйти до загрузки" });
  await f.sync.start();
  assert.equal(f.requests.length, 0);
  assert.equal(f.statuses.at(-1).revision, 10);
  f.change({ decision: "accepted" });
  await f.sync.settled();
  assert.deepEqual(f.requests[0], { job: JOB, body: {
    action: "decide", finding_id: "vlm:1", decision: "accepted", expected_revision: 10,
  } });
});

test("правка, принятие и Undo идут последовательно с серверной ревизией", async () => {
  let resolveFirst;
  const bodies = [];
  const f = fixture({ api: { command: async (_job, body) => {
    bodies.push(body);
    if (bodies.length === 1) await new Promise((resolve) => { resolveFirst = resolve; });
    return { job_id: JOB, revision: body.expected_revision + 1 };
  } } });
  await f.sync.start();
  f.change({ text: "Edited", normative_basis: "СП 2" });
  f.change({ decision: "accepted" });
  f.change({ decision: "pending" });
  assert.equal(bodies.length, 1);
  resolveFirst();
  await f.sync.settled();
  assert.deepEqual(bodies.map((body) => [body.action, body.expected_revision]), [
    ["edit", 10], ["decide", 11], ["reset", 12],
  ]);
  assert.equal(f.sync.pending, 0);
});

test("409 сохраняет локальную очередь и не повторяет устаревшую команду", async () => {
  let calls = 0;
  const f = fixture({ api: { command: async () => { calls += 1; throw Object.assign(new Error("Stale"), { status: 409 }); } } });
  await f.sync.start();
  f.change({ decision: "rejected" });
  await f.sync.settled();
  await f.sync.retry();
  assert.equal(calls, 1);
  assert.equal(f.sync.pending, 1);
  assert.equal(f.statuses.at(-1).mode, "conflict");
});

test("потерянный ответ не дублирует запись: retry сохраняет ожидаемую ревизию", async () => {
  const revisions = [];
  const f = fixture({ api: { command: async (_job, body) => {
    revisions.push(body.expected_revision);
    throw Object.assign(new Error("Lost"), { status: revisions.length === 1 ? 503 : 409 });
  } } });
  await f.sync.start();
  f.change({ decision: "accepted" });
  await f.sync.settled();
  assert.equal(revisions.length, 1);
  await f.sync.retry();
  assert.deepEqual(revisions, [10, 10]);
  assert.equal(f.statuses.at(-1).mode, "conflict");
});

test("закрытый серверный режим не подменяется успешным локальным сохранением", async () => {
  const f = fixture({ api: { open: async () => { throw new Error("Unavailable"); } } });
  await f.sync.start();
  f.change({ decision: "accepted" });
  assert.equal(f.statuses.at(-1).mode, "error");
  assert.equal(f.requests.length, 0);
  const local = fixture({ api: { config: async () => ({ enabled: false }) } });
  await local.sync.start();
  local.change({ decision: "accepted" });
  assert.equal(local.statuses.at(-1).mode, "local");
  assert.equal(local.requests.length, 0);
});

test("ответ старого задания не обновляет новый отчёт; очередь завершает сохранение", async () => {
  let resolve;
  const f = fixture({ api: { command: () => new Promise((done) => { resolve = done; }) } });
  await f.sync.start();
  f.change({ decision: "accepted" });
  f.sync.detach();
  const before = f.statuses.length;
  resolve({ job_id: JOB, revision: 11 });
  await f.sync.settled();
  assert.equal(f.statuses.length, before);
  assert.equal(f.sync.pending, 0);
});

test("неверное задание в ответе не считается восстановленным", async () => {
  const f = fixture({ api: { open: async () => ({ job_id: "other", revision: 10 }) } });
  await f.sync.start();
  assert.equal(f.statuses.at(-1).mode, "error");
});

test("Gold + геометрия передаются отдельно от оригиналов и тегов VLM", () => {
  const box = { x_min: 0, y_min: 0, x_max: 100, y_max: 100 };
  const gold = { ...entry, finding_id: "manual:id", origin: "manual", page_number: 1,
    decision: "accepted", regions: [box], callout_box: box };
  const added = reviewCommands([], [gold]);
  assert.deepEqual(added.map((row) => row.action), ["add", "decide"]);
  assert.equal(added[0].issue_box, box);
  const geometry = reviewCommands([gold], [{ ...gold, decision: "pending", regions: [{ ...box, x_max: 200 }] }]);
  assert.deepEqual(geometry.map((row) => row.action), ["geometry"]);
  assert.ok(added.every((row) => !Object.hasOwn(row, "actor") && !Object.hasOwn(row, "experience_tag")));
});

test("HTTP клиент не передаёт контекст инженера и одинаково разбирает ошибки", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response('{"detail":"Conflict"}', { status: 409 }); };
  try {
    await assert.rejects(createReviewApi().command(JOB, { action: "reset", finding_id: "vlm:1", expected_revision: 10 }), (error) => error.status === 409);
    assert.equal(requests[0].url, `/api/v1/analyses/${JOB}/review/commands`);
    assert.deepEqual(requests[0].options.headers, { "Content-Type": "application/json" });
    assert.equal(requests[0].options.cache, "no-store");
  } finally { globalThis.fetch = original; }
});
