// frontend/tests/reviewed-area-controls.test.js

/** Проверка областей VLM: явное действие, независимость от решения и очистка DOM-подписок. */

import assert from "node:assert/strict";
import test from "node:test";
import { mountAreaConfirmations } from "../src/js/features/review/area-controls.js";
import { FakeElement } from "./helpers/fake-dom.js";

const BOX = { x_min: 10.25, y_min: 20, x_max: 250.5, y_max: 100 };

function fixture(t, { decision = "pending", regions = [BOX], confirmation = null } = {}) {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  globalThis.window = { prompt: () => null };
  t.after(() => {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  });
  const root = new FakeElement();
  const preview = new FakeElement("p");
  preview.dataset.reviewPreview = "";
  preview.after = (node) => root.append(node);
  root.append(preview);
  const item = new FakeElement("article");
  item.className = "analysis-result__finding";
  item.dataset.findingId = "vlm:1";
  root.append(item);
  const finding = { finding_id: "vlm:1", origin: "vlm", decision,
    proposed_regions: regions.map((bbox) => ({ bbox })), display_regions: null };
  let session = { findings: [finding], area_confirmations: confirmation ? [confirmation] : [], pending_count: decision === "pending" ? 1 : 0 };
  const requests = [];
  const busy = [];
  let execute = async () => session;
  const controls = mountAreaConfirmations({ root,
    sync: { get session() { return session; }, run: async (command) => { requests.push(command); return execute(command); } },
    snapshot: () => [{ finding_id: "vlm:1", regions }], onBusy: (value) => busy.push(value),
  });
  controls.update({ mode: "saved", pending: 0, session });
  const button = item.children.find((child) => child.dataset.reviewAreaConfirmation === "vlm:1");
  const error = root.children.find((child) => child.getAttribute("role") === "alert");
  return { controls, button, error, requests, busy, finding,
    status(patch = {}) { controls.update({ mode: "saved", pending: 0, session, ...patch }); },
    confirm(value) { session = { ...session, area_confirmations: [value] }; },
    execute(callback) { execute = callback; },
    async click() { for (const handler of button.listeners.get("click") ?? []) await handler(); },
  };
}

test("область подтверждается отдельно от принятия замечания и сохраняет дробные координаты", async (t) => {
  const f = fixture(t);
  assert.equal(f.finding.decision, "pending");
  assert.equal(f.button.disabled, false);
  assert.equal(f.button.getAttribute("aria-pressed"), "false");
  await f.click();
  assert.deepEqual(f.requests, [{ action: "confirm_area", finding_id: "vlm:1",
    expected_confirmation_revision: 0, regions: [BOX], mode: "proposed", note: "" }]);
  assert.equal(f.finding.decision, "pending");
  assert.deepEqual(f.busy, [true, false]);
});

test("принятое замечание без координат остаётся только в текстовом PDF", async (t) => {
  const f = fixture(t, { decision: "accepted", regions: [] });
  assert.equal(f.button.disabled, true);
  assert.match(f.button.textContent, /только в тексте PDF/);
  await f.click();
  assert.equal(f.requests.length, 0);
});

test("локальный канал скрывает подтверждение, а сохранение и общая операция блокируют его", async (t) => {
  const f = fixture(t);
  f.status({ mode: "local" });
  assert.equal(f.button.hidden, true);
  assert.equal(f.button.disabled, true);
  for (const status of [{ pending: 1 }, { busy: true }, { mode: "saving" }, { mode: "conflict" }]) {
    f.status(status);
    assert.equal(f.button.disabled, true);
    await f.click();
  }
  assert.equal(f.requests.length, 0);
});

test("отзыв требует причины, использует текущую ревизию области и отменяется без записи", async (t) => {
  const f = fixture(t, { confirmation: { finding_id: "vlm:1", valid: true, revision: 4 } });
  assert.equal(f.button.getAttribute("aria-pressed"), "true");
  await f.click();
  assert.equal(f.requests.length, 0);
  globalThis.window.prompt = () => "   ";
  await f.click();
  assert.match(f.error.textContent, /причину/);
  assert.equal(f.requests.length, 0);
  globalThis.window.prompt = () => "  Рамка охватывает другой элемент  ";
  await f.click();
  assert.deepEqual(f.requests[0], { action: "revoke_area", finding_id: "vlm:1",
    expected_confirmation_revision: 4, reason: "Рамка охватывает другой элемент" });
});

test("инвалидация после правки сохраняет CAS области и повторная проверка требует причины", async (t) => {
  const changed = { ...BOX, x_min: 17.75 };
  const f = fixture(t, { regions: [changed], confirmation: { finding_id: "vlm:1", valid: false, revision: 6 } });
  f.finding.proposed_regions = [{ bbox: BOX }];
  f.finding.display_regions = [changed];
  f.status();
  assert.equal(f.button.getAttribute("aria-pressed"), "false");
  globalThis.window.prompt = () => null;
  await f.click();
  assert.equal(f.requests.length, 0);
  globalThis.window.prompt = () => "Рамка была шире обнаруженного дефекта";
  await f.click();
  assert.deepEqual(f.requests[0], { action: "confirm_area", finding_id: "vlm:1",
    expected_confirmation_revision: 6, regions: [changed], mode: "redrawn", note: "Рамка была шире обнаруженного дефекта" });
});

test("серверный конфликт виден пользователю и не создаёт повторную запись", async (t) => {
  const f = fixture(t);
  f.execute(async () => { throw Object.assign(new Error("Конфликт"), { detail: "Область изменена в другой вкладке" }); });
  await f.click();
  assert.match(f.error.textContent, /другой вкладке/);
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.busy, [true, false]);
});

test("новые метаданные сервера меняют отметку области без повторного монтирования", (t) => {
  const f = fixture(t);
  f.confirm({ finding_id: "vlm:1", valid: true, revision: 1 });
  f.status();
  assert.equal(f.button.getAttribute("aria-pressed"), "true");
  assert.match(f.button.textContent, /Отозвать/);
  f.confirm({ finding_id: "vlm:1", valid: false, revision: 1 });
  f.status();
  assert.equal(f.button.getAttribute("aria-pressed"), "false");
  assert.match(f.button.textContent, /Подтвердить область/);
});

test("смена отчёта снимает подписки и поздний ответ не обновляет старый DOM", async (t) => {
  const f = fixture(t);
  let finish;
  f.execute(() => new Promise((resolve) => { finish = resolve; }));
  const saving = f.click();
  assert.equal(f.requests.length, 1);
  assert.equal(typeof f.controls.dispose, "function", "Адаптер области должен поддерживать очистку при смене задания");
  f.controls.dispose();
  const before = f.busy.length;
  finish({});
  await saving;
  assert.equal((f.button.listeners.get("click") ?? []).length, 0);
  assert.equal(f.busy.length, before, "Ответ старой операции не должен менять состояние новой страницы");
});
