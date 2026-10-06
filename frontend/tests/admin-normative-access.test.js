// frontend/tests/admin-normative-access.test.js

/** Назначение удаления нормативных объектов в админке: автоматические роли, сохранение, отзыв и конфликты. */
import assert from "node:assert/strict";
import test from "node:test";
import { createNormativeAccessControl } from "../src/js/features/admin/normative-access.js";
import { changeNormativeAccess } from "../src/js/features/admin/api.js";
import { setCsrfToken } from "../src/js/features/auth/api.js";
import { FakeElement } from "./helpers/fake-dom.js";

globalThis.document = { createElement: (tag) => new FakeElement(tag) };
const USER = "11111111-1111-4111-8111-111111111111";
const designer = () => ({
  user_id: USER, authorization_version: 3, normative_access: false,
  normative_access_automatic: false, normative_access_editable: true,
});
const checkboxOf = (root) => root.children[0].children[0];
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("у админа галочка отмечена и отключена для изменения", () => {
  let calls = 0;
  const root = createNormativeAccessControl({ ...designer(), normative_access: true,
    normative_access_automatic: true, normative_access_editable: false }, {
    changeAccess: async () => { calls += 1; },
  });
  const checkbox = checkboxOf(root);
  assert.equal(checkbox.checked, true);
  assert.equal(checkbox.disabled, true);
  assert.equal(root.children[1].textContent, "По роли");
  checkbox.checked = false;
  checkbox.dispatch("change");
  assert.equal(checkbox.checked, true);
  assert.equal(calls, 0);
});

test("проектировщику доступ выдаётся и снимается с актуальной версией сервера", async () => {
  const user = designer();
  const calls = [];
  let refreshes = 0;
  const root = createNormativeAccessControl(user, {
    onChanged: async () => { refreshes += 1; },
    changeAccess: async (...args) => {
      calls.push(args);
      return { user: { authorization_version: args[2] + 1 }, normative_access: args[1],
        normative_access_automatic: false, normative_access_editable: true };
    },
  });
  const checkbox = checkboxOf(root);
  assert.equal(checkbox.checked, false);
  assert.equal(checkbox.disabled, false);
  checkbox.checked = true;
  checkbox.dispatch("change");
  assert.equal(checkbox.disabled, true);
  await settle();
  assert.equal(user.normative_access, true);
  assert.equal(user.authorization_version, 4);
  checkbox.checked = false;
  checkbox.dispatch("change");
  await settle();
  assert.deepEqual(calls, [[USER, true, 3], [USER, false, 4]]);
  assert.equal(user.normative_access, false);
  assert.equal(refreshes, 2);
});

test("ошибка сохранения возвращает подтверждённую галочку; конфликт требует обновления", async () => {
  for (const status of [503, 409]) {
    const user = designer();
    const root = createNormativeAccessControl(user, {
      changeAccess: async () => { throw { status }; },
    });
    const checkbox = checkboxOf(root);
    checkbox.checked = true;
    checkbox.dispatch("change");
    await settle();
    assert.equal(checkbox.checked, false);
    assert.equal(user.authorization_version, 3);
    assert.equal(checkbox.disabled, status === 409);
    assert.match(root.children[1].textContent, status === 409 ? /Обновите/ : /Не удалось/);
  }
});

test("повтор во время сохранения не отправляет второй запрос", async () => {
  let finish;
  let calls = 0;
  const user = designer();
  const root = createNormativeAccessControl(user, {
    changeAccess: () => {
      calls += 1;
      return new Promise((resolve) => { finish = resolve; });
    },
  });
  const checkbox = checkboxOf(root);
  checkbox.checked = true;
  checkbox.dispatch("change");
  checkbox.dispatch("change");
  assert.equal(calls, 1);
  finish({ user: { authorization_version: 4 }, normative_access: true,
    normative_access_automatic: false, normative_access_editable: true });
  await settle();
  assert.equal(checkbox.checked, true);
});

test("ошибка перезагрузки списка не отменяет уже сохранённое назначение", async () => {
  const user = designer();
  const root = createNormativeAccessControl(user, {
    changeAccess: async () => ({ user: { authorization_version: 4 }, normative_access: true,
      normative_access_automatic: false, normative_access_editable: true }),
    onChanged: async () => { throw new Error("Нет сети"); },
  });
  const checkbox = checkboxOf(root);
  checkbox.checked = true;
  checkbox.dispatch("change");
  await settle();
  assert.equal(checkbox.checked, true);
  assert.equal(checkbox.disabled, true);
  assert.equal(user.authorization_version, 4);
  assert.match(root.children[1].textContent, /сохранён/);
});

test("API передаёт только флаг и CAS с CSRF, без назначения роли", async () => {
  const previous = globalThis.fetch;
  let request;
  setCsrfToken("csrf-test");
  globalThis.fetch = async (url, options) => {
    request = { url, ...options };
    return new Response(JSON.stringify({ normative_access: true }), { status: 200 });
  };
  try {
    await changeNormativeAccess(USER, true, 3);
    assert.equal(request.url, `/api/v1/admin/users/${USER}/normative-access`);
    assert.equal(request.method, "PATCH");
    assert.equal(request.headers["X-CSRF-Token"], "csrf-test");
    assert.equal(request.credentials, "same-origin");
    assert.deepEqual(JSON.parse(request.body), { enabled: true, authorization_version: 3 });
  } finally { globalThis.fetch = previous; setCsrfToken(""); }
});
