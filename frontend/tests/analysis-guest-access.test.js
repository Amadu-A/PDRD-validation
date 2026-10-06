// frontend/tests/analysis-guest-access.test.js

/** Гостевой bearer ключ ограничен одним заданием и серверным сроком доступа. */
import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelAnalysis, getAnalysisResult, getAnalysisStatus, getAnalysisVisualization,
} from "../src/js/features/analysis/api.js";
import {
  consumeGuestAccessFromUrl, guestAccessFor, guestAccessHeaders,
  rememberGuestAccess, temporaryAnalysisLink,
} from "../src/js/features/analysis/guest-access.js";
import { restoreAnalysisReport } from "../src/js/features/analysis/restore.js";
import { createModal } from "../src/js/components/modal.js";
import { appendGuestShareLink } from "../src/js/features/analysis/share-link.js";
import { FakeElement } from "./helpers/fake-dom.js";

const JOB = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
const STORED = "44444444-4444-4444-8444-444444444444";
const TOKEN = "sufficiently_long_random_guest_token_111";

function future() { return new Date(Date.now() + 60_000).toISOString(); }

test("временная ссылка держит ключ во fragment и убирает его до запроса", () => {
  const previousStorage = globalThis.sessionStorage;
  const values = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  try {
    const expiresAt = future();
    rememberGuestAccess(JOB, TOKEN, expiresAt);
    const link = temporaryAnalysisLink(JOB, { origin: "https://pdrd.example" });
    assert.equal(new URL(link).search, "");
    assert.match(new URL(link).hash, /access_token=/);
    let cleaned = "";
    const loaded = consumeGuestAccessFromUrl({ href: link }, {
      replaceState: (_state, _title, url) => { cleaned = url; },
    });
    assert.equal(loaded, JOB);
    assert.equal(cleaned, `/?job_id=${JOB}`);
    assert.equal(guestAccessFor(JOB).expiresAt, expiresAt);
    assert.equal(guestAccessHeaders(OTHER)["X-PDRD-Analysis-Access"], undefined);
    assert.equal(guestAccessHeaders(JOB)["X-PDRD-Analysis-Access"], TOKEN);
  } finally {
    globalThis.sessionStorage = previousStorage;
  }
});

test("истёкший ключ не восстанавливается из sessionStorage", () => {
  const previousStorage = globalThis.sessionStorage;
  const values = new Map([[`pdrd.analysis.guest.v1.${OTHER}`, JSON.stringify({
    token: TOKEN, expiresAt: "2020-01-01T00:00:00Z",
  })]]);
  globalThis.sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  try {
    assert.equal(guestAccessFor(OTHER), null);
    assert.equal(values.size, 0);
    assert.throws(() => rememberGuestAccess(OTHER, TOKEN, "2020-01-01T00:00:00Z"), /срок/);
  } finally {
    globalThis.sessionStorage = previousStorage;
  }
});

test("после обновления страницы ключ восстанавливается из sessionStorage своего задания", () => {
  const previousStorage = globalThis.sessionStorage;
  const expiresAt = future();
  const values = new Map([[`pdrd.analysis.guest.v1.${STORED}`, JSON.stringify({
    token: TOKEN, expiresAt,
  })]]);
  globalThis.sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  try {
    assert.equal(guestAccessFor(STORED)?.token, TOKEN);
    assert.equal(guestAccessHeaders(STORED)["X-PDRD-Analysis-Access"], TOKEN);
  } finally {
    globalThis.sessionStorage = previousStorage;
  }
});

test("статус, progress, результат, визуализация и отмена передают ключ только своего задания", async () => {
  rememberGuestAccess(JOB, TOKEN, future());
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({ status: "completed" }), { status: 200 });
  };
  try {
    await getAnalysisStatus(JOB);
    await getAnalysisResult(JOB);
    await getAnalysisVisualization(JOB);
    await cancelAnalysis(JOB);
    assert.equal(calls.length, 5);
    assert.deepEqual(calls.map(({ url }) => url), [
      `/api/v1/analyses/${JOB}`, `/api/v1/analyses/${JOB}/progress`,
      `/api/v1/analyses/${JOB}/result`, `/api/v1/analyses/${JOB}/visualization`,
      `/api/v1/analyses/${JOB}/cancel`,
    ]);
    for (const { url, options } of calls) {
      assert.equal(options.headers["X-PDRD-Analysis-Access"], TOKEN);
      assert.doesNotMatch(url, /access_token/);
    }
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("переход по гостевой ссылке восстанавливает задание и скрывает ключ из адреса", async () => {
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  const link = temporaryAnalysisLink(JOB, { origin: "https://pdrd.example" });
  let cleaned = "";
  const calls = [];
  const messages = [];
  globalThis.window = {
    location: { href: link },
    history: { replaceState: (_state, _title, url) => { cleaned = url; } },
  };
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response('{"status":"cancelled"}', { status: 200 });
  };
  try {
    await restoreAnalysisReport({
      show: (message) => messages.push(message),
      showError: (error) => { throw error; },
    });
    assert.equal(cleaned, `/?job_id=${JOB}`);
    assert.equal(calls.length, 2);
    assert.ok(calls.every(({ options }) => options.headers["X-PDRD-Analysis-Access"] === TOKEN));
    assert.match(messages.at(-1), /отменён/);
  } finally {
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  }
});

test("модальное окно копирует временную ссылку вместо одного job ID", async () => {
  const previousDocument = globalThis.document;
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  let copied = "";
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { clipboard: { writeText: async (value) => { copied = value; } } },
  });
  try {
    const copyButton = new FakeElement("button");
    const copyStatus = new FakeElement();
    const modal = createModal({
      modalElement: new FakeElement(), textElement: new FakeElement(),
      submitButton: new FakeElement("button"), jobElement: new FakeElement(),
      jobIdElement: new FakeElement(), copyButton, copyStatusElement: copyStatus,
    });
    const link = temporaryAnalysisLink(JOB, { origin: "https://pdrd.example" });
    modal.setJobId(JOB, { shareUrl: link, expiresAt: future() });
    assert.match(copyButton.textContent, /временную ссылку/);
    await copyButton.listeners.get("click")[0]();
    assert.equal(copied, link);
    assert.match(copyStatus.textContent, /скопирована/);
  } finally {
    globalThis.document = previousDocument;
    if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
    else delete globalThis.navigator;
  }
});

test("после анализа временная ссылка остаётся в отчёте", () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  globalThis.window = { location: { origin: "https://pdrd.example" } };
  try {
    rememberGuestAccess(JOB, TOKEN, future());
    const report = new FakeElement();
    appendGuestShareLink(report, JOB);
    assert.equal(report.children.length, 1);
    const link = report.children[0].children[2];
    assert.equal(new URL(link.href).search, "");
    assert.match(new URL(link.href).hash, /access_token=/);
    const authenticatedReport = new FakeElement();
    appendGuestShareLink(authenticatedReport, OTHER);
    assert.equal(authenticatedReport.children.length, 0);
  } finally {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  }
});
