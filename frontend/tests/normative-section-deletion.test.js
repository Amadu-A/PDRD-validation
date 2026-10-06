// frontend/tests/normative-section-deletion.test.js

/** Функциональные проверки каскадного удаления и UI незавершённой очистки. */
import assert from "node:assert/strict";
import test from "node:test";
import { createNormativeCatalog } from "../src/js/features/normative/catalog.js";
import { FakeElement } from "./helpers/fake-dom.js";

/** Создаёт реальные обработчики каталога и изолированный HTTP/DOM transport. */
function fixture(t, deleting = false) {
  const previous = { document: globalThis.document, window: globalThis.window, fetch: globalThis.fetch };
  t.after(() => { Object.assign(globalThis, previous); });
  const controls = new Map();
  const root = new FakeElement();
  for (const name of ["section-select", "section-create", "section-rename", "section-delete", "category-create", "select-all", "clear-all", "upload-zone", "file-input", "tree", "status"]) {
    const element = new FakeElement();
    controls.set(name, element);
    root.selectors.set(`[data-normative-${name}]`, element);
  }
  let removed = false;
  const requests = [];
  const confirmations = [];
  const selected = [];
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  globalThis.window = { confirm: (message) => { confirmations.push(message); return true; }, clearTimeout() {}, setTimeout() {} };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method ?? "GET" });
    if (options.method === "DELETE") {
      removed = true;
      return Response.json({ section_id: "selected", deleted: true });
    }
    if (url.endsWith("/sections")) return Response.json(removed ? [] : [{ section_id: "selected", name: "АР", deleting }]);
    return Response.json([]);
  };
  const catalog = createNormativeCatalog(root, { onSectionChange: async (id) => { selected.push(id); } });
  return { catalog, controls, requests, confirmations, selected };
}

/** Дожидается реального async обработчика события минимального DOM. */
async function dispatch(element, event) {
  for (const listener of element.listeners.get(event) ?? []) await listener({});
}

test("подтверждение сообщает о личных пакетах и очистке файлов и индекса", async (t) => {
  const f = fixture(t);
  await f.catalog.start();
  f.controls.get("section-select").value = "selected";
  await dispatch(f.controls.get("section-select"), "change");
  await dispatch(f.controls.get("section-delete"), "click");
  assert.match(f.confirmations[0], /личные пакеты/);
  assert.match(f.confirmations[0], /PDF предпросмотры/);
  assert.match(f.confirmations[0], /поисковый индекс/);
  assert.equal(f.requests.filter((request) => request.method === "DELETE").length, 1);
  assert.equal(f.catalog.getSelection(), null);
});

test("tombstone можно выбрать только для повторного удаления, без загрузки и промпта", async (t) => {
  const f = fixture(t, true);
  await f.catalog.start();
  f.controls.get("section-select").value = "selected";
  await dispatch(f.controls.get("section-select"), "change");
  assert.equal(f.controls.get("file-input").disabled, true);
  assert.equal(f.controls.get("section-rename").disabled, true);
  assert.equal(f.controls.get("section-delete").disabled, false);
  assert.equal(f.catalog.getSelection().deleting, true);
  assert.equal(f.selected.at(-1), null);
  assert.equal(f.requests.filter((request) => /categories|documents/.test(request.url)).length, 0);
  await dispatch(f.controls.get("section-delete"), "click");
  assert.equal(f.catalog.getSelection(), null);
});
