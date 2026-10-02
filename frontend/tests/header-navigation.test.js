// frontend/tests/header-navigation.test.js

/** Одноразовый переход к окну входа не повторяется после обновления главной страницы. */
import assert from "node:assert/strict";
import test from "node:test";

import { bindSiteHeader } from "../src/js/features/auth/header.js";
import { clearSession } from "../src/js/features/auth/session.js";
import { FakeElement } from "./helpers/fake-dom.js";

test("параметр auth удаляется до открытия диалога, остальные параметры сохраняются", async () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const root = new FakeElement();
  const dialog = new FakeElement("dialog");
  const loginForm = new FakeElement("form");
  const registerForm = new FakeElement("form");
  const selected = [];
  for (const selector of [
    "[data-open-login]", "[data-open-register]", "[data-header-logout]",
    "[data-header-identity]", "[data-account-link]", "[data-admin-link]",
  ]) root.selectors.set(selector, new FakeElement());
  for (const [selector, element] of [
    ["[data-auth-title]", new FakeElement()],
    ["[data-auth-message]", new FakeElement()],
    ["[data-auth-hint]", new FakeElement()],
    ["[data-auth-login-form]", loginForm],
    ["[data-auth-register-form]", registerForm],
    ["[data-auth-close]", new FakeElement()],
  ]) dialog.selectors.set(selector, element);
  dialog.lists.set("[data-auth-tab]", []);
  globalThis.document = { querySelector: () => dialog };
  globalThis.window = {
    location: { search: "?auth=register&mode=compact", pathname: "/", hash: "#analysis" },
    history: { replaceState: (_state, _title, url) => selected.push(url) },
  };
  globalThis.fetch = async () => new Response('{"authenticated":false}', { status: 200 });
  try {
    await bindSiteHeader(root);
    assert.equal(dialog.open, true);
    assert.equal(registerForm.hidden, false);
    assert.deepEqual(selected, ["/?mode=compact#analysis"]);
  } finally {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
    clearSession();
  }
});

test("выход очищает профиль только после ответа сервера и возвращает к анализу", async () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const root = new FakeElement();
  const navigations = [];
  const calls = [];
  for (const selector of [
    "[data-open-login]", "[data-open-register]", "[data-header-logout]",
    "[data-header-identity]", "[data-account-link]", "[data-admin-link]",
  ]) root.selectors.set(selector, new FakeElement());
  globalThis.document = { querySelector: () => null };
  globalThis.window = { location: { search: "" } };
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(url.endsWith("/session")
      ? JSON.stringify({ authenticated: true, csrf_token: "token", user: {
        user_id: "u1", display_name: "Иван", roles: ["designer"], permissions: [],
      } })
      : "{}", { status: 200 });
  };
  try {
    await bindSiteHeader(root, { navigate: (url) => navigations.push(url) });
    const logoutButton = root.querySelector("[data-header-logout]");
    assert.equal(logoutButton.hidden, false);
    await logoutButton.listeners.get("click")[0]();
    assert.equal(logoutButton.hidden, true);
    assert.deepEqual(navigations, ["/"]);
    assert.equal(calls[1].url, "/api/v1/auth/logout");
    assert.equal(calls[1].options.headers["X-CSRF-Token"], "token");
  } finally {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
    clearSession();
  }
});
