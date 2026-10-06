// frontend/tests/analysis-history.test.js

/** Регрессии истории: пагинация, выход, старый ответ, PDF утверждённой ревизии. */
import assert from "node:assert/strict";
import test from "node:test";
import { createAnalysisHistory, historyDetails } from "../src/js/features/analysis/history.js";
import { historyApi } from "../src/js/features/analysis/history-api.js";
import { FakeElement } from "./helpers/fake-dom.js";

const job = "11111111-1111-4111-8111-111111111111";
const owner = { authenticated: true, user: { user_id: "owner" } };
const guest = { authenticated: false, user: null };
const item = { job_id: job, file_name: "ВК.pdf", created_at: "2026-10-06T09:00:00Z",
  status: "completed", pages_count: 18, findings_count: 7, section_name: "ОВ",
  review_status: "approved", review_revision: 4, result_available: true,
  pdf_url: `/api/v1/analyses/${job}/reviewed-pdf`, pdf_kind: "reviewed" };
const tick = () => new Promise((resolve) => setImmediate(resolve));

function view(api, download = () => {}) {
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const root = new FakeElement();
  let publish;
  const controller = createAnalysisHistory(root, { limit: 5, api, download,
    subscribe(listener) { publish = listener; listener(guest); return () => {}; },
  });
  return { root, controller, publish: (state) => publish(state),
    rows: () => root.children[1].children, controls: () => root.children[2].children };
}

test("история сохраняет ноль замечаний и показывает состояние Review", () => {
  const text = historyDetails({ ...item, findings_count: 0 });
  assert.match(text, /Страниц: 18/);
  assert.match(text, /Замечаний: 0/);
  assert.match(text, /Human Review: утверждён/);
  assert.doesNotMatch(historyDetails({ ...item, findings_count: null }), /Замечаний: 0/);
});

test("гость не вызывает API, владельцу доступна пагинация и открытие по job_id", async () => {
  const calls = [];
  const ui = view({ async list(query) { calls.push(query); return { items: [{ ...item, file_name: '<img src=x onerror="bad">' }], has_more: query.offset === 0 }; } });
  assert.equal(ui.root.hidden, true);
  assert.equal(calls.length, 0);
  ui.publish(owner); await tick();
  assert.equal(ui.root.hidden, false);
  assert.equal(ui.rows()[0].children[0].textContent.includes('<img src=x'), true);
  assert.equal(ui.rows()[0].children[0].children.length, 0);
  assert.equal(ui.rows()[0].children[2].children[0].href, `/?job_id=${job}`);
  ui.controls()[1].click(); await tick();
  assert.deepEqual(calls, [{ limit: 5, offset: 0 }, { limit: 5, offset: 5 }]);
  assert.equal(ui.controls()[1].disabled, true);
  ui.controls()[0].click(); await tick();
  assert.deepEqual(calls[2], { limit: 5, offset: 0 });
});

test("ответ после выхода не восстанавливает историю прежнего пользователя", async () => {
  let finish;
  const ui = view({ list: () => new Promise((resolve) => { finish = resolve; }) });
  ui.publish(owner);
  ui.publish(guest);
  finish({ items: [item], has_more: false }); await tick();
  assert.equal(ui.root.hidden, true);
  assert.equal(ui.rows().length, 0);
});

test("ответ старого пользователя не заменяет историю после смены аккаунта", async () => {
  const pending = [];
  const ui = view({ list: () => new Promise((resolve) => pending.push(resolve)) });
  ui.publish(owner);
  ui.publish({ authenticated: true, user: { user_id: "another" } });
  pending[1]({ items: [{ ...item, file_name: "Другой.pdf" }], has_more: false }); await tick();
  pending[0]({ items: [item], has_more: false }); await tick();
  assert.match(ui.rows()[0].children[0].textContent, /Другой.pdf/);
});

test("сбой истории допускает повторное чтение, PDF использует сохранённую ревизию", async () => {
  let failed = true;
  const pdfCalls = [], downloads = [];
  const ui = view({ async list() {
    if (failed) throw { detail: "История временно недоступна" };
    return { items: [item], has_more: false };
  }, async pdf(value) { pdfCalls.push(value); return "saved-pdf"; } }, (file) => downloads.push(file));
  ui.publish(owner); await tick();
  assert.match(ui.root.children[0].textContent, /временно недоступна/);
  failed = false; ui.controls()[2].click(); await tick();
  ui.rows()[0].children[2].children[1].click(); await tick();
  assert.deepEqual(pdfCalls, [item]);
  assert.deepEqual(downloads, ["saved-pdf"]);
});

test("HTTP-клиент истории не отправляет owner и скачивает PDF с CAS без нового анализа", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    if (url.includes("/history?")) return { ok: true, text: async () => '{"items":[]}' };
    return { ok: true, blob: async () => new Blob(["%PDF-1.7\n"]),
      headers: new Map([["content-type", "application/pdf"]]) };
  };
  await historyApi.list({ limit: 5, offset: 10 });
  await historyApi.pdf(item);
  assert.equal(requests[0].url, "/api/v1/analyses/history?limit=5&offset=10");
  assert.equal(requests[0].options.cache, "no-store");
  assert.equal(requests[1].url, `/api/v1/analyses/${job}/reviewed-pdf`);
  assert.equal(requests[1].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[1].options.body), { expected_revision: 4 });
});


test("истёкшие исходники не скрывают результат и показывают причину отсутствия PDF", async () => {
  const ui = view({ async list() { return { items: [{ ...item, pdf_url: null, source_artifacts_expired: true }], has_more: false }; } });
  ui.publish(owner); await tick();
  assert.match(ui.rows()[0].children[2].textContent, /30 дней/);
  assert.match(ui.rows()[0].children[2].textContent, /Review сохранены/);
  assert.equal(ui.rows()[0].children[3].children.length, 1);
  assert.equal(ui.rows()[0].children[3].children[0].textContent, "Открыть");
});
