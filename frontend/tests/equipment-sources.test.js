// frontend/tests/equipment-sources.test.js

/** Проверяет серверный контракт каталога EQ-источников и право доступа. */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
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




test("ссылка на источники расположена под историей; поле причины отсутствует в HTML", () => {
  const html = readFileSync(new URL("../src/account.html", import.meta.url), "utf8");
  const sidebar = html.slice(html.indexOf('<aside class="portal-nav"'), html.indexOf("</aside>"));
  assert.match(sidebar, /href="#equipment-sources" data-account-equipment-sources-link hidden/);
  assert.ok(sidebar.indexOf("data-account-equipment-sources-link") > sidebar.indexOf("data-account-history-link"));
  assert.ok(sidebar.indexOf("data-account-equipment-sources-link") < sidebar.indexOf('href="#security"'));
  assert.match(html, /id="equipment-sources" data-equipment-sources/);
  assert.doesNotMatch(html, /name="reason"/);
});



test("гость не получает ссылку на источники даже с клиентским permission", async () => {
  const root = new FakeElement();
  const link = new FakeElement("a");
  root.selectors.set("[data-account-equipment-sources-link]", link);
  await renderEquipmentSources(root, { authenticated: false, user: { permissions: ["equipment_sources.manage"] } });
  assert.equal(link.hidden, true);
});
