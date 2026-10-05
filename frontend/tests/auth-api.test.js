// frontend/tests/auth-api.test.js

/** Проверяет cookie-контракт входа, CSRF и отсутствие доверия к неподтверждённой сессии. */
import assert from "node:assert/strict";
import test from "node:test";
import { login, logout, register, setCsrfToken, verifyEmail } from "../src/js/features/auth/api.js";
import { clearSession, currentSession, refreshSession } from "../src/js/features/auth/session.js";

test("вход и регистрация отправляют секреты только в JSON того же origin", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response("{}", { status: 200 });
  };
  setCsrfToken("");
  await login("i.mein", "secret-password");
  await register("Иван", "ivan@example.test", "another-password");
  assert.equal(calls[0].url, "/api/v1/auth/login");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    login: "i.mein", password: "secret-password",
  });
  assert.equal(calls[1].url, "/api/v1/auth/register");
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    display_name: "Иван", email: "ivan@example.test", password: "another-password",
  });
  assert.doesNotMatch(calls[0].url + calls[1].url, /secret-password|another-password/);
});

test("сессия обновляет CSRF заголовок, а 401 сбрасывает профиль", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({
      authenticated: true,
      csrf_token: "session-csrf",
      user: { user_id: "u1", display_name: "Иван", roles: ["designer"],
        permissions: ["review.gold.create"] },
      session: { session_id: "s1" },
    }), { status: 200 });
  };
  const state = await refreshSession();
  assert.equal(state.user.display_name, "Иван");
  assert.equal(state.user.permissions[0], "review.gold.create");
  await logout();
  assert.equal(calls[1].options.headers["X-CSRF-Token"], "session-csrf");
  globalThis.fetch = async () => new Response('{"detail":"Нет сессии"}', { status: 401 });
  await refreshSession();
  assert.equal(currentSession().authenticated, false);
  clearSession();
});

test("подтверждение email отправляет код только в теле POST", async () => {
  const calls = [];
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response("{}", { status: 200 });
  };
  try {
    await verifyEmail("opaque-token");
    assert.equal(calls[0].url, "/api/v1/auth/verify-email");
    assert.equal(calls[0].options.method, "POST");
    assert.deepEqual(JSON.parse(calls[0].options.body), { token: "opaque-token" });
    assert.doesNotMatch(calls[0].url, /opaque-token/);
  } finally {
    globalThis.fetch = previousFetch;
  }
});


test("страница HTTP не отправляет пароль входа или регистрации", async () => {
  const previousLocation = globalThis.location;
  const previousFetch = globalThis.fetch;
  let requests = 0;
  globalThis.location = { protocol: "http:" };
  globalThis.fetch = async () => {
    requests += 1;
    throw new Error("Пароль не должен уходить в сеть");
  };
  try {
    await assert.rejects(login("i.mein", "secret-password"), /только по HTTPS/);
    await assert.rejects(register("Иван", "ivan@example.test", "secret-password"), /только по HTTPS/);
    assert.equal(requests, 0);
  } finally {
    if (previousLocation === undefined) delete globalThis.location;
    else globalThis.location = previousLocation;
    globalThis.fetch = previousFetch;
  }
});

test("страница HTTPS отправляет вход обычным защищённым маршрутом", async () => {
  const previousLocation = globalThis.location;
  const previousFetch = globalThis.fetch;
  globalThis.location = { protocol: "https:" };
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response("{}", { status: 200 });
  };
  try {
    await login("admin", "test-password");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/v1/auth/login");
    assert.equal(calls[0].options.credentials, "same-origin");
  } finally {
    if (previousLocation === undefined) delete globalThis.location;
    else globalThis.location = previousLocation;
    globalThis.fetch = previousFetch;
  }
});
