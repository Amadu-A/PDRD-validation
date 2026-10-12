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


test("обычный пользователь с назначением видит доверенные сайты; отзыв скрывает блок", async (t) => {
  const previous = { document: globalThis.document, fetch: globalThis.fetch };
  t.after(() => Object.assign(globalThis, previous));
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const root = new FakeElement();
  const navigationLink = new FakeElement("a");
  root.selectors.set("[data-account-equipment-sources-link]", navigationLink);
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
  assert.equal(navigationLink.hidden, false);
  const row = root.querySelector("[data-equipment-source-list]").children[0];
  assert.equal(row.children[0].textContent, "CHINT · chint.ru");
  assert.match(row.children[1].textContent, /trusted/);
  await renderEquipmentSources(root, { authenticated: true, user: { ...user, permissions: [] } });
  assert.equal(root.querySelector("[data-equipment-sources]").hidden, true);
  assert.equal(navigationLink.hidden, true);
  assert.equal(requests, 2);
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

test("редактирование и добавление источника работают без ввода причины, аудит и CSRF сохраняются", async (t) => {
  const previous = { document: globalThis.document, fetch: globalThis.fetch, FormData: globalThis.FormData };
  t.after(() => { Object.assign(globalThis, previous); setCsrfToken(""); });
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const root = new FakeElement();
  for (const selector of ["sources", "source-status", "source-list", "filter-status", "filter-query", "filter-apply", "manufacturers", "source-form"]) {
    root.selectors.set(`[data-equipment-${selector}]`, new FakeElement());
  }
  const source = { id: "source-id", manufacturer: "CHINT", hostname: "chint.ru", status: "pending", enabled: true };
  const writes = [];
  globalThis.fetch = async (url, options) => {
    if (options.method === "PUT") {
      writes.push({ payload: JSON.parse(options.body), csrf: options.headers["X-CSRF-Token"] });
      return new Response(JSON.stringify(source));
    }
    return new Response(JSON.stringify(url.endsWith("manufacturers") ? [{ name: "CHINT" }] : [source]));
  };
  setCsrfToken("session-csrf-ui");
  await renderEquipmentSources(root, { authenticated: true, user: { permissions: ["equipment_sources.manage"] } });
  const controls = root.querySelector("[data-equipment-source-list]").children[0].children[2];
  assert.equal(controls.children.length, 5);
  controls.children[0].value = "trusted";
  await controls.children.find((node) => node.textContent === "Сохранить").onclick();
  assert.equal(writes[0].payload.status, "trusted");

  const form = root.querySelector("[data-equipment-source-form]");
  let resets = 0;
  form.reset = () => { resets += 1; };
  globalThis.FormData = class {
    constructor(element) { assert.equal(element, form); }
    get(name) { return { manufacturer: "IEK", hostname: "iek.ru", status: "trusted" }[name] ?? null; }
  };
  await form.onsubmit({ preventDefault() {} });
  assert.equal(writes.length, 2);
  assert.equal(writes[1].payload.hostname, "iek.ru");
  assert.equal(resets, 1);
  for (const write of writes) {
    assert.equal(write.csrf, "session-csrf-ui");
    assert.equal(write.payload.reason, "Сохранение источника через личный кабинет");
  }
});

test("гость не получает ссылку на источники даже с клиентским permission", async () => {
  const root = new FakeElement();
  const link = new FakeElement("a");
  root.selectors.set("[data-account-equipment-sources-link]", link);
  await renderEquipmentSources(root, { authenticated: false, user: { permissions: ["equipment_sources.manage"] } });
  assert.equal(link.hidden, true);
});
