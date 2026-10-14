// frontend/tests/equipment-source-catalog.test.js

/** Функциональные проверки немедленных решений и восстановления подтверждённого UI. */
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { renderEquipmentSources, siteHostname } from "../src/js/features/account/equipment-sources.js";
import { auditTable } from "../src/js/features/account/equipment-source-view.js";

const session = { authenticated: true, user: { id: "me", display_name: "Иван", permissions: ["equipment_sources.manage"] } };
const source = (id, status = "trusted", enabled = true) => ({ id, manufacturer: "CHINT", hostname: `${id}.example.com`, status, enabled, allow_http: true });
const all = (root) => [root, ...root.children.flatMap(all)];
const find = (root, text) => all(root).find((item) => item.textContent === text);
const byClass = (root, name) => all(root).filter((item) => item.className === name);

function setup(t, initial = [source("one"), source("two", "blocked", false), source("new", "pending")]) {
  /** Изолирует сессию, API и дерево DOM; настоящий SQL проверяется отдельно. */
  const previous = { document: globalThis.document, fetch: globalThis.fetch };
  t.after(() => Object.assign(globalThis, previous));
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  const root = new FakeElement(), section = new FakeElement(), app = new FakeElement(), status = new FakeElement();
  root.selectors.set("[data-equipment-sources]", section);
  root.selectors.set("[data-equipment-source-app]", app);
  root.selectors.set("[data-equipment-source-status]", status);
  section.append(app, status);
  const writes = [];
  let fail = false, delay = null;
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === "PUT") {
      const payload = JSON.parse(options.body); writes.push(payload);
      if (delay) await delay;
      if (fail) return new Response(JSON.stringify({ detail: "Отказ сохранения" }), { status: 503 });
      const old = initial.find((item) => item.hostname === payload.hostname);
      const saved = { ...old, ...payload, id: old?.id || "added" };
      if (old) Object.assign(old, saved); else initial.push(saved);
      return Response.json(saved);
    }
    if (url.endsWith("manufacturers")) return Response.json([{ name: "CHINT", aliases: [] }]);
    if (url.endsWith("audit")) return Response.json([{ previous_status: "trusted", previous_enabled: true,
      new_status: "blocked", new_enabled: false, reason: "Тестовое решение", actor: "me", occurred_at: "2026-10-14T08:00:00Z" }]);
    return Response.json(initial);
  };
  return { root, section, app, status, writes, fail: () => { fail = true; }, delay: (promise) => { delay = promise; } };
}

test("группы, отдельные новые сайты, фильтры и поиск", async (t) => {
  const s = setup(t); await renderEquipmentSources(s.root, session);
  assert.equal(byClass(s.app, "equipment-sources__group").length, 1);
  assert.equal(byClass(s.app, "equipment-sources__pending").length, 1);
  assert.equal(byClass(s.app, "equipment-sources__row").length, 2);
  find(s.app, "Не используем (1)").onclick();
  assert.equal(byClass(s.app, "equipment-sources__row").length, 1);
  const search = all(s.app).find((item) => item.type === "search");
  search.value = "missing"; search.oninput();
  assert.equal(byClass(s.app, "equipment-sources__row").length, 0);
  assert.equal(byClass(s.app, "equipment-sources__pending").length, 0);
});

test("переключатель запрещает сайт, не передаёт HTTP и сохраняется после загрузки", async (t) => {
  const s = setup(t, [source("one")]); await renderEquipmentSources(s.root, session);
  const toggle = all(s.app).find((item) => item.dataset.sourceToggle);
  toggle.checked = false; await toggle.onchange();
  assert.equal(s.writes[0].status, "blocked"); assert.equal(s.writes[0].enabled, false);
  assert.equal("allow_http" in s.writes[0], false);
  await renderEquipmentSources(s.root, session);
  assert.equal(all(s.app).find((item) => item.dataset.sourceToggle).checked, false);
  const off = all(s.app).find((item) => item.dataset.sourceToggle); off.checked = true; await off.onchange();
  assert.equal(s.writes[1].status, "trusted");
});

test("ошибка возвращает состояние; повторный клик заблокирован", async (t) => {
  const s = setup(t, [source("one")]); await renderEquipmentSources(s.root, session);
  s.fail(); let resolve; s.delay(new Promise((done) => { resolve = done; }));
  const toggle = all(s.app).find((item) => item.dataset.sourceToggle); toggle.checked = false;
  const pending = toggle.onchange(); await toggle.onchange();
  assert.equal(s.writes.length, 1);
  assert.equal(all(s.app).find((item) => item.dataset.sourceToggle).disabled, true);
  resolve(); await pending;
  assert.equal(all(s.app).find((item) => item.dataset.sourceToggle).checked, true);
  assert.match(s.status.textContent, /Отказ сохранения/);
});

for (const allowed of [true, false]) test(`решение нового сайта ${allowed}`, async (t) => {
  const s = setup(t, [source("new", "pending")]); await renderEquipmentSources(s.root, session);
  await find(s.app, allowed ? "Использовать" : "Не использовать").onclick();
  assert.equal(s.writes[0].status, allowed ? "trusted" : "blocked");
  assert.equal(byClass(s.app, "equipment-sources__pending").length, 0);
  assert.equal(byClass(s.app, "equipment-sources__row").length, 1);
});

test("добавление, дубликаты и история без технических полей", async (t) => {
  const s = setup(t); await renderEquipmentSources(s.root, session);
  find(s.app, "＋ Добавить сайт").onclick();
  const dialog = byClass(s.section, "equipment-sources__dialog")[0];
  const inputs = all(dialog).filter((item) => item.tagName === "INPUT");
  inputs[0].value = "CHINT"; inputs[1].value = "https://one.example.com/catalog";
  const form = all(dialog).find((item) => item.tagName === "FORM");
  await form.onsubmit({ preventDefault() {} }); assert.equal(s.writes.length, 0);
  assert.match(byClass(dialog, "equipment-sources__error")[0].textContent, /уже добавлен/);
  inputs[1].value = "www.one.example.com"; await form.onsubmit({ preventDefault() {} });
  assert.equal(s.writes[0].hostname, "www.one.example.com"); assert.equal(s.writes[0].create_only, true);
  await find(s.app, "История изменений").onclick();
  const history = byClass(s.section, "equipment-sources__dialog").at(-1);
  assert.ok(all(history).some((item) => item.textContent.includes("Запретил использование")));
  assert.ok(find(history, "Иван")); assert.ok(find(history, "Тестовое решение"));
});

test("отзыв доступа лишает действия старые кнопки и скрывает каталог", async (t) => {
  const s = setup(t); await renderEquipmentSources(s.root, session);
  const toggle = all(s.app).find((item) => item.dataset.sourceToggle);
  await renderEquipmentSources(s.root, { authenticated: false });
  await toggle.onchange(); assert.equal(s.writes.length, 0); assert.equal(s.section.hidden, true);
});

test("точный HTTPS hostname и запрет небезопасных адресов", () => {
  assert.equal(siteHostname("WWW.Example.com/path"), "www.example.com");
  assert.equal(siteHostname("example.com"), "example.com");
  for (const value of ["http://example.com", "https://u:p@example.com", "localhost", "127.0.0.1", "https://example.com:8443", "*.example.com", "site.internal"]) {
    assert.throws(() => siteHostname(value));
  }
});

test("отклонение нового сайта подписано как запрет, изменение HTTP — как настройка", (t) => {
  setup(t);
  const audit = auditTable([
    { previous_status: "pending", previous_enabled: true, new_status: "blocked", new_enabled: false },
    { previous_status: "trusted", previous_enabled: true, new_status: "trusted", new_enabled: true,
      previous_allow_http: false, new_allow_http: true },
  ], session);
  assert.ok(all(audit).some((item) => item.textContent.startsWith("Запретил использование")));
  assert.ok(all(audit).some((item) => item.textContent.startsWith("Изменил настройки сайта")));
});
