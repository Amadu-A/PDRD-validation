// frontend/tests/review-pdf-label.test.js

/** Не допускает выдачи старого annotated PDF за результат Human Review. */

import assert from "node:assert/strict";
import test from "node:test";
import { appendAnnotatedPdfDownload } from "../src/js/features/analysis/pdf-export.js";
import { rememberGuestAccess } from "../src/js/features/analysis/guest-access.js";

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.textContent = "";
    this.className = "";
    this.listeners = new Map();
    this.attributes = new Map();
  }
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  click() { this.clicked = true; }
  remove() { this.removed = true; }
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

test("гостевой PDF скачивается по заголовку без token в URL", async () => {
  const jobId = "33333333-3333-4333-8333-333333333333";
  const token = "another_long_random_guest_token_333";
  rememberGuestAccess(jobId, token,
    new Date(Date.now() + 60_000).toISOString());
  const parent = new Element("div");
  appendAnnotatedPdfDownload(parent, {
    jobId, payload: { status: "completed", source_mode: "pdf_only" },
  });
  const link = parent.children[0].children.find((child) => child.tagName === "A");
  assert.equal(link.href, `/api/v1/analyses/${jobId}/annotated-pdf`);
  const previousFetch = globalThis.fetch;
  const previousWindow = globalThis.window;
  const previousCreate = URL.createObjectURL;
  const previousRevoke = URL.revokeObjectURL;
  let request;
  let temporary;
  let revoked = "";
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response("%PDF-1.7\ncontent", { status: 200, headers: {
      "content-type": "application/pdf",
    } });
  };
  globalThis.window = { setTimeout: (callback) => callback() };
  globalThis.document.body = { append: (element) => { temporary = element; } };
  URL.createObjectURL = () => "blob:automatic";
  URL.revokeObjectURL = (url) => { revoked = url; };
  try {
    await link.listeners.get("click")({ preventDefault() {} });
    assert.equal(request.url, link.href);
    assert.equal(request.options.headers["X-PDRD-Analysis-Access"], token);
    assert.equal(temporary.clicked, true);
    assert.equal(revoked, "blob:automatic");
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.window = previousWindow;
    URL.createObjectURL = previousCreate;
    URL.revokeObjectURL = previousRevoke;
  }
});
