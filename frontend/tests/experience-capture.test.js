// frontend/tests/experience-capture.test.js

/** Перенос после утверждения, ручной повтор, ошибки, блокировка и демонтаж. */
import assert from "node:assert/strict";
import test from "node:test";
import { mountExperienceCapture } from "../src/js/features/experience/capture-controls.js";
import { reviewedPdfAvailability } from "../src/js/features/review/pdf-model.js";
import { createExperienceApi } from "../src/js/features/experience/api.js";
import { deferred, Node, setup } from "./experience-dom.js";

function controls(options = {}) {
  setup(); const anchor = new Node("button"); const sync = { session: { revision: 4, approved_revision: 4, pending_count: 0 } };
  const events = []; const api = { capture: async () => ({ created: 2, eligible: 2, excluded: 1 }) };
  const controller = mountExperienceCapture({ root: { querySelector: () => anchor }, jobId: "job", sync, api,
    onBusy: (value) => events.push(value), ...options });
  controller.update({ mode: "saved", pending: 0, session: sync.session });
  return { controller, button: anchor.afterNodes[0], message: anchor.afterNodes[1], sync, api, events };
}

test("локальный PDF объясняет серверный режим и отсутствие автоматического переноса", () => {
  const state = reviewedPdfAvailability({ mode: "local" });
  assert.equal(state.enabled, false);
  assert.match(state.message, /8081/);
  assert.match(state.message, /автоматически.*не переносятся/);
  const ui = controls(); ui.controller.update({ mode: "local" }); assert.equal(ui.button.hidden, true);
});

test("утверждение сохраняет Experience один раз, уже выполненный автоматический перенос не повторяется", async () => {
  const ui = controls(); const calls = [];
  ui.sync.session.approved_revision = null;
  ui.sync.run = async (command) => { calls.push(command); return { revision: 5, approved_revision: 5,
    experience_capture: { status: "saved", created: 2, eligible: 2, excluded: 1 } }; };
  ui.api.capture = async () => { throw new Error("Лишний повтор"); };
  await ui.button.click();
  assert.deepEqual(calls, [{ action: "approve" }]);
  assert.deepEqual(ui.events, [true, false]);
  assert.match(ui.message.textContent, /пригодных 2/);
});

test("после ошибки автоматического переноса доступен явный повтор", async () => {
  const ui = controls(); ui.api.capture = async () => { throw { detail: "PNG недоступен" }; };
  await ui.button.click(); assert.match(ui.message.textContent, /PNG недоступен/);
  assert.equal(ui.button.disabled, false);
  ui.api.capture = async () => ({ created: 1, eligible: 1, excluded: 0 });
  await ui.button.click(); assert.match(ui.message.textContent, /пригодных 1/);
});

test("двойной клик не дублирует запрос и поздний ответ не меняет снятый Review", async () => {
  const ui = controls(); const pending = deferred(); let calls = 0;
  ui.api.capture = () => { calls += 1; return pending.promise; };
  const first = ui.button.click(); ui.button.click(); ui.controller.dispose();
  pending.resolve({ created: 1, eligible: 1, excluded: 0 }); await first;
  assert.equal(calls, 1); assert.equal(ui.button.removed, true);
  assert.deepEqual(ui.events, [true]);
});

test("сетевой адаптер отправляет только ревизию и отвергает HTML вместо ZIP", async () => {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push([url, options]); return { ok: true, text: async () => "{}", headers: { get: () => "text/html" }, blob: async () => new Blob(["<html>"]) };
  };
  const api = createExperienceApi();
  await api.capture("job/id", 5);
  assert.equal(calls[0][0], "/api/v1/experience/capture/job%2Fid");
  assert.deepEqual(JSON.parse(calls[0][1].body), { expected_revision: 5 });
  await assert.rejects(api.export({ tag: "edited:rejected", active: false }), /неверного формата/);
  assert.match(calls[1][0], /active=false/);
});
