// frontend/tests/review-pdf-label.test.js

/** Не допускает выдачи старого annotated PDF за результат Human Review. */

import assert from "node:assert/strict";
import test from "node:test";
import { appendAnnotatedPdfDownload } from "../src/js/features/analysis/pdf-export.js";

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.textContent = "";
    this.className = "";
  }
  append(...nodes) { this.children.push(...nodes); }
}

globalThis.document = { createElement: (tag) => new Element(tag) };

test("автоматический PDF помечен как исходный, финальный review отключён", () => {
  const parent = new Element("div");
  appendAnnotatedPdfDownload(parent, {
    jobId: "a/b",
    payload: { status: "completed", source_mode: "pdf_only" },
  });
  assert.equal(parent.children.length, 1);
  const section = parent.children[0];
  const link = section.children.find((child) => child.tagName === "A");
  const finalButton = section.children.find((child) => child.tagName === "BUTTON");
  assert.equal(link.href, "/api/v1/analyses/a%2Fb/annotated-pdf");
  assert.match(link.textContent, /без решений пользователя/);
  assert.match(section.children[1].textContent, /не учитывает Wise, Bad, Edited/);
  assert.equal(finalButton.disabled, true);
  assert.match(finalButton.title, /в текстовом списке/);
});

test("экспорт не отображается до завершения анализа и при CAD-only", () => {
  const parent = new Element("div");
  appendAnnotatedPdfDownload(parent, {
    jobId: "one", payload: { status: "processing", source_mode: "pdf_only" },
  });
  appendAnnotatedPdfDownload(parent, {
    jobId: "one", payload: { status: "completed", source_mode: "cad_only" },
  });
  assert.equal(parent.children.length, 0);
});