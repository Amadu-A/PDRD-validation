// frontend/tests/equipment-sources.test.js

/** Проверяет серверный контракт каталога EQ-источников и право доступа. */
import assert from "node:assert/strict";
import test from "node:test";
import {
  canManageEquipmentSources,
  listEquipmentSources,
  saveEquipmentSource,
} from "../src/js/features/account/equipment-sources.js";
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
