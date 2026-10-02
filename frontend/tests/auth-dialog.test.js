// frontend/tests/auth-dialog.test.js

/** Ошибка входа очищает пароль и остаётся сообщением в модальном окне. */
import assert from "node:assert/strict";
import test from "node:test";

import { bindAuthDialog } from "../src/js/features/auth/dialog.js";
import { FakeElement } from "./helpers/fake-dom.js";

test("неверный пароль не ломает модальное окно входа", async () => {
  const dialog = new FakeElement("dialog");
  const title = new FakeElement();
  const message = new FakeElement();
  const hint = new FakeElement();
  const loginForm = new FakeElement("form");
  const registerForm = new FakeElement("form");
  const close = new FakeElement("button");
  const submit = new FakeElement("button");
  const login = { value: "i.mein" };
  const password = { value: "wrong-password" };
  loginForm.elements = {
    namedItem: (name) => ({ login, password })[name],
  };
  loginForm.selectors.set('[type="submit"]', submit);
  for (const [selector, element] of [
    ["[data-auth-title]", title],
    ["[data-auth-message]", message],
    ["[data-auth-hint]", hint],
    ["[data-auth-login-form]", loginForm],
    ["[data-auth-register-form]", registerForm],
    ["[data-auth-close]", close],
  ]) dialog.selectors.set(selector, element);
  dialog.lists.set("[data-auth-tab]", []);
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: "Неверные учётные данные" }),
    { status: 401 },
  );

  bindAuthDialog(dialog);
  const handler = loginForm.listeners.get("submit")[0];
  await handler({ preventDefault() {} });

  assert.equal(message.textContent, "Неверные учётные данные");
  assert.equal(password.value, "");
  assert.equal(submit.disabled, false);
});

test("успешная регистрация предлагает подтвердить почту и очищает пароль", async () => {
  const dialog = new FakeElement("dialog");
  const loginForm = new FakeElement("form");
  const registerForm = new FakeElement("form");
  const message = new FakeElement();
  const submit = new FakeElement("button");
  const fields = {
    display_name: { value: "Иван Мейн" },
    email: { value: "ivan@example.test" },
    password: { value: "temporary-secret" },
  };
  registerForm.elements = { namedItem: (name) => fields[name] };
  registerForm.selectors.set('[type="submit"]', submit);
  for (const [selector, element] of [
    ["[data-auth-title]", new FakeElement()],
    ["[data-auth-message]", message],
    ["[data-auth-hint]", new FakeElement()],
    ["[data-auth-login-form]", loginForm],
    ["[data-auth-register-form]", registerForm],
    ["[data-auth-close]", new FakeElement()],
  ]) dialog.selectors.set(selector, element);
  dialog.lists.set("[data-auth-tab]", []);
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response("{}", { status: 202 });
  };
  try {
    bindAuthDialog(dialog);
    await registerForm.listeners.get("submit")[0]({ preventDefault() {} });
    assert.equal(calls[0].url, "/api/v1/auth/register");
    assert.match(message.textContent, /Проверьте почту/);
    assert.equal(fields.password.value, "");
    assert.equal(submit.disabled, false);
  } finally {
    globalThis.fetch = previousFetch;
  }
});
