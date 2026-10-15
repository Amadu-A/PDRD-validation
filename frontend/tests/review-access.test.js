// frontend/tests/review-access.test.js

/** Регрессия: изменения ревью доступны только с серверным разрешением. */
import assert from "node:assert/strict";
import test from "node:test";
import { createReviewController } from "../src/js/features/review/controller.js";
import { FakeElement } from "./helpers/fake-dom.js";
import { bindAuthorizedReview, mountAuthorizedReview } from "../src/js/features/review/access.js";
import { clearSession, refreshSession } from "../src/js/features/auth/session.js";
import { createReviewPersistence } from "../src/js/features/review/persistence.js";

globalThis.document = { createElement: (tag) => new FakeElement(tag) };

test("право decide без Gold монтирует Review; поздняя авторизация не требует перезагрузки отчёта", async (t) => {
  const previous = globalThis.fetch;
  t.after(() => { globalThis.fetch = previous; clearSession(); });
  clearSession();
  const calls = [];
  const controller = { mount: (...args) => calls.push(args), clear() {} };
  const access = bindAuthorizedReview(controller);
  t.after(() => access.dispose());
  const root = new FakeElement();
  access.mount(root, { jobId: "job" });
  assert.equal(calls.length, 0);
  globalThis.fetch = async () => new Response(JSON.stringify({ authenticated: true,
    user: { user_id: "engineer", permissions: ["review.findings.decide"] } }));
  await refreshSession();
  assert.equal(calls.length, 1);
  await refreshSession();
  assert.equal(calls.length, 1);
  assert.equal(mountAuthorizedReview(controller, root, {}), true);
});

test("ошибка открытия видна без лишних кнопок; повторное открытие отчёта разблокирует контролы", async (t) => {
  const previous = globalThis.window;
  globalThis.window = { addEventListener() {}, location: { href: "https://pdrd.test/" }, history: { replaceState() {} } };
  t.after(() => { globalThis.window = previous; });
  const root = new FakeElement();
  const visualization = new FakeElement(); visualization.className = "analysis-result__visualization";
  const article = new FakeElement(); article.dataset.findingId = "f1";
  const text = new FakeElement(); text.className = "analysis-result__annotation-title"; text.textContent = "Назначение В2";
  article.append(text); visualization.append(article); root.append(visualization);
  let opens = 0; const commands = [];
  const persistence = createReviewPersistence({ getCapabilities: () => ({ canCreateGold: false, canDecide: true, canDownloadReview: false }),
    api: { config: async () => ({ enabled: true }), open: async () => {
      if (++opens === 1) throw new Error("Ошибка восстановления геометрии");
      return { job_id: "job", revision: 0, findings: [{ finding_id: "f1", origin: "vlm", original_text: text.textContent,
        original_basis: "", text: text.textContent, normative_basis: "", decision: "pending", proposed_regions: [] }] };
    }, command: async (_job, body) => { commands.push(body); return { job_id: "job", revision: 1 }; } },
  });
  persistence.mount(root, { jobId: "job" });
  t.after(() => persistence.clear());
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  await settle();
  let accept = article.querySelectorAll("[data-review-action]").find((button) => button.dataset.reviewAction === "accept");
  assert.equal(accept.disabled, true);
  assert.equal(root.querySelectorAll("button").some((button) => /Повторить подключение|Повторить сохранение|Загрузить серверную/.test(button.textContent)), false);
  assert.match(root.querySelector("[data-review-preview]").textContent, /Ошибка восстановления геометрии/);
  persistence.mount(root, { jobId: "job" }); await settle();
  accept = article.querySelectorAll("[data-review-action]").find((button) => button.dataset.reviewAction === "accept");
  assert.equal(opens, 2); assert.equal(accept.disabled, false);
  accept.click(); await settle();
  assert.equal(commands[0].action, "decide"); assert.equal(commands[0].expected_revision, 0);
});

test("без review.findings.decide кнопки решения не действуют", () => {
  const root = new FakeElement();
  const visualization = new FakeElement();
  root.selectors.set(".analysis-result__visualization", visualization);
  const item = new FakeElement();
  item.dataset.findingId = "f1";
  const text = new FakeElement("span");
  text.textContent = "Автоматическое замечание";
  item.selectors.set(
    ".analysis-result__annotation-title, .analysis-result__group-member-text", text,
  );
  visualization.lists.set("[data-finding-id]", [item]);
  createReviewController({ capabilities: {
    canCreateGold: true, canDecide: false,
  } }).mount(root);
  const controls = item.children.find((child) => child.dataset.reviewControls !== undefined);
  const accept = controls.children.find((child) => child.dataset.reviewAction === "accept");
  const reject = controls.children.find((child) => child.dataset.reviewAction === "reject");
  const edit = controls.children.find((child) => child.dataset.reviewAction === "edit");
  assert.equal(accept.hidden, true);
  assert.equal(reject.hidden, true);
  assert.equal(edit.hidden, true);
  accept.click();
  reject.click();
  assert.equal(item.dataset.reviewDecision, "pending");
});


test("назначенный доступ к ревью открывает решения и правки VLM-замечаний", () => {
  const root = new FakeElement();
  const visualization = new FakeElement();
  root.selectors.set(".analysis-result__visualization", visualization);
  const item = new FakeElement();
  item.dataset.findingId = "f1";
  const text = new FakeElement("span");
  text.textContent = "Автоматическое замечание";
  item.selectors.set(".analysis-result__annotation-title, .analysis-result__group-member-text", text);
  visualization.lists.set("[data-finding-id]", [item]);
  const controller = createReviewController({ capabilities: { canCreateGold: true, canDecide: true } });
  controller.mount(root);
  const controls = item.children.find((child) => child.dataset.reviewControls !== undefined);
  const action = (name) => controls.children.find((child) => child.dataset.reviewAction === name);
  assert.equal(action("accept").hidden, false);
  assert.equal(action("reject").hidden, false);
  assert.equal(action("edit").hidden, false);
  action("accept").click();
  assert.equal(item.dataset.reviewDecision, "accepted");
  controller.setCapabilities({ canCreateGold: true, canDecide: false });
  assert.equal(action("accept").hidden, true);
  assert.equal(action("edit").disabled, true);
  assert.equal(item.dataset.reviewDecision, "accepted");
  controller.setCapabilities({ canCreateGold: false, canDecide: true });
  assert.equal(action("accept").hidden, false);
  assert.equal(item.dataset.reviewDecision, "accepted");
});
