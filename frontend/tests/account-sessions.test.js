// frontend/tests/account-sessions.test.js

/** Ответ списка устройств не должен появляться в кабинете после выхода. */
import assert from "node:assert/strict";
import test from "node:test";

import { renderSessions } from "../src/js/features/account/sessions.js";

function fixture() {
  const list = { children: [], replaceChildren(...children) { this.children = children; }, append(child) { this.children.push(child); } };
  const summary = { textContent: "" };
  const status = { textContent: "" };
  const button = { hidden: true, disabled: false, onclick: null };
  const elements = new Map([
    ["[data-session-list]", list], ["[data-session-summary]", summary],
    ["[data-session-status]", status], ["[data-account-logout-all]", button],
  ]);
  return { root: { querySelector: (selector) => elements.get(selector) }, list, summary, status, button };
}

test("поздний ответ списка устройств не отображается после смены сессии", async () => {
  let finishRequest;
  const previousFetch = globalThis.fetch;
  globalThis.fetch = () => new Promise((resolve) => { finishRequest = resolve; });
  const { root, list, summary, button } = fixture();
  try {
    const pending = renderSessions(root, { authenticated: true, session: { session_id: "old" } });
    assert.equal(typeof button.onclick, "function");
    await renderSessions(root, { authenticated: false });
    finishRequest(new Response(JSON.stringify({ sessions: [{
      session_id: "old", created_at: "2026-10-01T00:00:00Z",
      absolute_expires_at: "2026-10-02T00:00:00Z",
    }] }), { status: 200 }));
    await pending;
    assert.equal(list.children.length, 0);
    assert.match(summary.textContent, /После входа/);
    assert.equal(button.onclick, null);
  } finally {
    globalThis.fetch = previousFetch;
  }
});
