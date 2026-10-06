// frontend/tests/auth-session-races.test.js

/** Проверяет, что ответы старых HTTP запросов не возвращают уже закрытую сессию. */
import assert from "node:assert/strict";
import test from "node:test";

import { clearSession, currentSession, refreshSession } from "../src/js/features/auth/session.js";

test("поздний ответ проверки сессии не восстанавливает профиль после выхода", async () => {
  let finishRequest;
  const previousFetch = globalThis.fetch;
  globalThis.fetch = () => new Promise((resolve) => { finishRequest = resolve; });
  try {
    const pending = refreshSession();
    clearSession();
    finishRequest(new Response(JSON.stringify({
      authenticated: true,
      user: { user_id: "old", display_name: "Старый пользователь" },
    }), { status: 200 }));
    await pending;
    assert.equal(currentSession().authenticated, false);
  } finally {
    globalThis.fetch = previousFetch;
    clearSession();
  }
});

test("поздняя ошибка старой проверки не скрывает новую сессию", async () => {
  let rejectRequest;
  const previousFetch = globalThis.fetch;
  globalThis.fetch = () => new Promise((_resolve, reject) => { rejectRequest = reject; });
  try {
    const pending = refreshSession();
    clearSession();
    rejectRequest(new Error("Старое соединение разорвано"));
    await assert.doesNotReject(pending);
    assert.equal(currentSession().authenticated, false);
  } finally {
    globalThis.fetch = previousFetch;
    clearSession();
  }
});

test("одновременные проверки принимают ответ последнего запроса", async () => {
  const resolvers = [];
  const previousFetch = globalThis.fetch;
  globalThis.fetch = () => new Promise((resolve) => { resolvers.push(resolve); });
  try {
    const first = refreshSession();
    const second = refreshSession();
    resolvers[0](new Response('{"authenticated":false}', { status: 200 }));
    await first;
    resolvers[1](new Response(JSON.stringify({
      authenticated: true,
      user: { user_id: "new", display_name: "Новый пользователь" },
    }), { status: 200 }));
    await second;
    assert.equal(currentSession().user.user_id, "new");
  } finally {
    globalThis.fetch = previousFetch;
    clearSession();
  }
});
