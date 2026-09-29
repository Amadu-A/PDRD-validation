// frontend/tests/experience-capture.test.js

/** Автоматический перенос опыта из сценария PDF, повтор и отдельный HTTP-адаптер. */
import assert from "node:assert/strict";
import test from "node:test";
import { captureApprovedExperience, experienceCaptureMessage } from "../src/js/features/experience/capture.js";
import { reviewedPdfAvailability } from "../src/js/features/review/pdf-model.js";
import { createExperienceApi } from "../src/js/features/experience/api.js";

test("недоступный Review объясняет проблему без другого порта или туннеля", () => {
  const state = reviewedPdfAvailability({ mode: "local" });
  assert.equal(state.enabled, false);
  assert.match(state.message, /недоступен/);
  assert.doesNotMatch(state.message, /8081|SSH|туннель|локальный предпросмотр/i);
});

test("новое утверждение использует выполненный сервером перенос без дублирующего запроса", async () => {
  const saved = { status: "saved", created: 2, eligible: 2, excluded: 1 };
  const result = await captureApprovedExperience({ jobId: "job", approvedNow: true,
    session: { revision: 5, experience_capture: saved },
    api: { capture: () => { throw new Error("Лишний запрос"); } } });
  assert.equal(result, saved);
  assert.match(experienceCaptureMessage(result), /проверенные примеры: 2/);
});

test("ошибка автоматического переноса сохраняется с рекомендацией повторить скачивание", async () => {
  const error = { status: "error", message: "Review утверждён, crop временно недоступен." };
  const result = await captureApprovedExperience({ jobId: "job", approvedNow: true,
    session: { revision: 5, experience_capture: error },
    api: { capture: () => { throw new Error("Неявный повтор"); } } });
  assert.equal(result, error);
  assert.match(experienceCaptureMessage(result), /crop.*Повторите скачивание/);
});

test("скачивание уже утверждённой редакции повторяет идемпотентный перенос", async () => {
  const calls = [];
  const result = await captureApprovedExperience({ jobId: "job", approvedNow: false,
    session: { revision: 5, approved_revision: 5, experience_capture: { status: "saved" } },
    api: { capture: async (...args) => { calls.push(args); return { created: 0, eligible: 2, excluded: 1 }; } } });
  assert.deepEqual(calls, [["job", 5]]);
  assert.equal(result.status, "saved");
  assert.equal(result.created, 0);
});

test("перезагрузка утверждённого Review не требует отдельной кнопки для сохранения Experience", async () => {
  const calls = [];
  const api = { capture: async () => { throw { detail: "PNG недоступен" }; } };
  const options = { jobId: "job", session: { revision: 8, approved_revision: 8 }, approvedNow: false, api };
  const failed = await captureApprovedExperience(options);
  assert.match(experienceCaptureMessage(failed), /PNG недоступен.*скачивание/);
  api.capture = async (...args) => { calls.push(args); return { created: 1, eligible: 1, excluded: 0 }; };
  const saved = await captureApprovedExperience(options);
  assert.equal(saved.status, "saved");
  assert.deepEqual(calls, [["job", 8]]);
});

test("HTTP capture отправляет только ревизию; неверный экспорт не скачивается", async (t) => {
  const previous = globalThis.fetch;
  t.after(() => { globalThis.fetch = previous; });
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
