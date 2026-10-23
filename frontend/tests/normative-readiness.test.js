// frontend/tests/normative-readiness.test.js

/** Готовность обычного поиска не подменяет N-first, неизвестный статус не становится обязательным. */
import assert from "node:assert/strict";
import test from "node:test";
import { documentReadinessText, sectionReadinessText } from "../src/js/features/normative/readiness.js";
import { normativeCoverageText } from "../src/js/features/analysis/normative-coverage.js";
import { createNormativeCatalog } from "../src/js/features/normative/catalog.js";
import { FakeElement } from "./helpers/fake-dom.js";

test("весь поиск READY при нуле атомов явно показывает неполную проверку", () => {
  const documents = Array.from({ length: 11 }, () => ({ search_state: "ready", atomic_state: "not_indexed" }));
  assert.match(sectionReadinessText({ documents }), /Поиск: 11\/11.*N-first: 0\/11.*неполной/);
  assert.match(documentReadinessText(documents[0]), /не подготовлен.*не подтверждён/);
});

test("частичное извлечение и нормативные свидетельства показываются отдельно", () => {
  const item = { atomic_state: "ready", source_coverage: "incomplete", requirements: 340, requirements_supported: 8, status_evidence_count: 1 };
  assert.match(documentReadinessText(item), /извлечение неполное.*340.*8.*по пунктам/);
  assert.match(sectionReadinessText({ documents: [item] }), /N-first: 0\/1/);
});

test("результат не объявляет fallback полноценной нормативной проверкой", () => {
  assert.match(normativeCoverageText({ normative_coverage: "incomplete", findings: [] }), /неполная/);
  assert.match(normativeCoverageText({}), /неизвестна/);
  assert.match(normativeCoverageText({ normative_coverage: "complete" }), /нормативный статус оцениваются отдельно/);
});

test("модуль каталога загружается со всеми реальными импортами", async () => {
  const module = await import("../src/js/features/normative/catalog.js");
  assert.equal(typeof module.createNormativeCatalog, "function");
});

test("зависшая сводка N-first не блокирует выбор раздела и основной каталог", async (t) => {
  const previous = { document: globalThis.document, window: globalThis.window, fetch: globalThis.fetch };
  t.after(() => Object.assign(globalThis, previous));
  const controls = new Map();
  const root = new FakeElement();
  for (const name of ["section-select", "section-create", "section-rename", "section-delete", "category-create", "select-all", "clear-all", "upload-zone", "file-input", "tree", "status"]) {
    const element = new FakeElement();
    controls.set(name, element);
    root.selectors.set(`[data-normative-${name}]`, element);
  }
  globalThis.document = { createElement: (tag) => new FakeElement(tag), createElementNS: (_ns, tag) => new FakeElement(tag) };
  globalThis.window = { clearTimeout() {}, setTimeout() {} };
  globalThis.fetch = async (url) => {
    if (url.endsWith("/atomic-readiness")) return new Promise(() => {});
    return Response.json(url.endsWith("/sections") ? [{ section_id: "section", name: "ИОС2" }] : []);
  };
  const catalog = createNormativeCatalog(root, { onSectionChange: async () => {},
    getSession: () => ({ authenticated: true, user: { permissions: ["admin.access"] } }), subscribe: () => () => {},
  });
  await catalog.start();
  controls.get("section-select").value = "section";
  for (const listener of controls.get("section-select").listeners.get("change") ?? []) await listener({});
  assert.equal(controls.get("file-input").disabled, false);
  assert.match(controls.get("tree").children[0].textContent, /не получена/);
});


test("диагностика доступна только admin и исчезает при выходе даже после позднего ответа", async (t) => {
  const previous = { document: globalThis.document, window: globalThis.window, fetch: globalThis.fetch };
  t.after(() => Object.assign(globalThis, previous));
  const root = new FakeElement(); const controls = new Map();
  for (const name of ["section-select", "section-create", "section-rename", "section-delete", "category-create", "select-all", "clear-all", "upload-zone", "file-input", "tree", "status"]) {
    const element = new FakeElement(); controls.set(name, element);
    root.selectors.set(`[data-normative-${name}]`, element);
  }
  globalThis.document = { createElement: (tag) => new FakeElement(tag), createElementNS: (_ns, tag) => new FakeElement(tag) };
  globalThis.window = { clearTimeout() {}, setTimeout() {} };
  let session = { authenticated: false }; let changed; let requests = 0; let resolveReadiness;
  globalThis.fetch = async (url) => {
    if (url.endsWith("/atomic-readiness")) { requests += 1; return new Promise((resolve) => { resolveReadiness = resolve; }); }
    if (url.endsWith("/sections")) return Response.json([{ section_id: "section", name: "ИОС2" }]);
    return Response.json(url.endsWith("/documents") ? [{ document_id: "doc", category_id: null, original_name: "СП.pdf", mime_type: "application/pdf", index_status: "ready", index_error: "Служебная диагностика" }] : []);
  };
  const catalog = createNormativeCatalog(root, { onSectionChange: async () => {}, getSession: () => session,
    subscribe: (callback) => { changed = callback; return () => {}; } });
  await catalog.start();
  controls.get("section-select").value = "section";
  for (const listener of controls.get("section-select").listeners.get("change") ?? []) await listener({});
  const tree = controls.get("tree");
  const text = (node) => [node.textContent, ...node.children.map(text)].join(" ");
  assert.equal(requests, 0); assert.match(text(tree), /Готов/);
  assert.doesNotMatch(text(tree), /N-first|атом|Служебная диагностика/);
  session = { authenticated: true, user: { permissions: ["normative.read"] } }; changed();
  assert.equal(requests, 0);
  session = { authenticated: true, user: { permissions: ["admin.access"] } }; changed();
  await new Promise(setImmediate);
  assert.equal(requests, 1); assert.match(text(tree), /N-first/);
  session = { authenticated: false }; changed();
  resolveReadiness(Response.json({ sections: [], documents: [{ document_id: "doc", atomic_state: "ready", requirements: 100 }] }));
  await new Promise(setImmediate);
  assert.doesNotMatch(text(tree), /N-first|атом|Служебная диагностика/);
  assert.match(text(tree), /Готов/);
  catalog.dispose();
});
