// frontend/tests/equipment-sources.test.js

/** Проверяет серверный контракт каталога EQ-источников и право доступа. */
import assert from "node:assert/strict";
import test from "node:test";
import {
  canManageEquipmentSources,
  listEquipmentSources,
  saveEquipmentSource,
  renderEquipmentSources,
} from "../src/js/features/account/equipment-sources.js";
import { FakeElement } from "./helpers/fake-dom.js";
import { setCsrfToken } from "../src/js/features/auth/api.js";

test("право управления EQ не появляется от включения unsafe режима", () => {
  assert.equal(canManageEquipmentSources({
    authenticated: true,
    user: { permissions: ["analysis.run"] },
  }), false);
  assert.equal(canManageEquipmentSources({
    authenticated: true,
    user: { permissions: ["equipment_sources.manage"] },
  }), true);
});

test("каталог фильтруется, изменение передаёт CSRF и причину", async () => {
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  setCsrfToken("session-csrf");
  try {
    await listEquipmentSources("pending", "mean well");
    await saveEquipmentSource({
      manufacturer: "MEAN WELL",
      hostname: "www.meanwell.com",
      status: "trusted",
      enabled: true,
      allow_http: false,
      reason: "Проверен официальный сайт",
    });
    assert.equal(calls[0].url,
      "/api/v1/equipment-sources?source_status=pending&query=mean+well");
    assert.equal(calls[1].options.method, "PUT");
    assert.equal(calls[1].options.headers["X-CSRF-Token"], "session-csrf");
    assert.equal(JSON.parse(calls[1].options.body).reason, "Проверен официальный сайт");
  } finally {
    setCsrfToken("");
    globalThis.fetch = previousFetch;
  }
});


test("обычный пользователь с назначением видит доверенные сайты; отзыв скрывает блок", async (t) => {
  const previous = { document: globalThis.document, fetch: globalThis.fetch };
  t.after(() => Object.assign(globalThis, previous));
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const root = new FakeElement();
  for (const key of ["sources", "source-status", "source-list", "filter-status", "filter-query", "filter-apply", "manufacturers", "source-form"]) {
    const selector = key === "sources" ? "[data-equipment-sources]"
      : key === "manufacturers" ? "[data-equipment-manufacturers]"
      : `[data-equipment-${key}]`;
    root.selectors.set(selector, new FakeElement());
  }
  let requests = 0;
  globalThis.fetch = async (url) => {
    requests += 1;
    return new Response(JSON.stringify(url.endsWith("manufacturers")
      ? [{ name: "CHINT" }]
      : [{ id: "domain", manufacturer: "CHINT", hostname: "chint.ru", status: "trusted", enabled: true }]), { status: 200 });
  };
  const user = { roles: ["designer"], permissions: ["equipment_sources.manage"] };
  await renderEquipmentSources(root, { authenticated: true, user });
  assert.equal(root.querySelector("[data-equipment-sources]").hidden, false);
  const row = root.querySelector("[data-equipment-source-list]").children[0];
  assert.equal(row.children[0].textContent, "CHINT · chint.ru");
  assert.match(row.children[1].textContent, /trusted/);
  await renderEquipmentSources(root, { authenticated: true, user: { ...user, permissions: [] } });
  assert.equal(root.querySelector("[data-equipment-sources]").hidden, true);
  assert.equal(requests, 2);
});
