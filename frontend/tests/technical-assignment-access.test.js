// frontend/tests/technical-assignment-access.test.js

/** Проверяет 24-часовой доступ к ТЗ без ключа в URL и изоляцию разных файлов. */
import assert from "node:assert/strict";
import test from "node:test";

import {
  forgetTechnicalAssignmentAccess,
  rememberPreparedTechnicalAssignment,
  technicalAssignmentAccessFor,
  technicalAssignmentAccessHeaders,
} from "../src/js/features/technical_assignment/access.js";
import { openTechnicalAssignmentCitation } from "../src/js/features/technical_assignment/citation.js";
import {
  appendTechnicalAssignmentPayload,
  technicalAssignmentAccessError,
} from "../src/js/features/technical_assignment/form-payload.js";

const FIRST = "11111111-1111-4111-8111-111111111111";
const SECOND = "22222222-2222-4222-8222-222222222222";
const TOKEN = "v1.1791000000.signed-technical-assignment";
const future = () => new Date(Date.now() + 60_000).toISOString();

test("ключ ТЗ остаётся у своего UUID и удаляется после истечения срока", () => {
  const previousStorage = globalThis.sessionStorage;
  const values = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  try {
    rememberPreparedTechnicalAssignment({
      technical_assignment_id: FIRST,
      access_token: TOKEN,
      access_expires_at: future(),
    });
    assert.equal(technicalAssignmentAccessFor(FIRST), TOKEN);
    assert.equal(technicalAssignmentAccessFor(SECOND), null);
    assert.deepEqual(technicalAssignmentAccessHeaders(FIRST), {
      "X-PDRD-Technical-Assignment-Access": TOKEN,
    });
    assert.deepEqual(technicalAssignmentAccessHeaders(SECOND), {});
    assert.equal(values.size, 1);
    forgetTechnicalAssignmentAccess(FIRST);
    assert.equal(technicalAssignmentAccessFor(FIRST), null);
    assert.equal(values.size, 0);
    assert.throws(() => rememberPreparedTechnicalAssignment({
      technical_assignment_id: FIRST,
      access_token: TOKEN,
      access_expires_at: "2020-01-01T00:00:00Z",
    }), /действующий доступ/);
    assert.equal(values.size, 0);
  } finally {
    globalThis.sessionStorage = previousStorage;
  }
});

test("анализ передаёт ключ ТЗ в multipart и останавливается при его утрате", () => {
  rememberPreparedTechnicalAssignment({
    technical_assignment_id: FIRST,
    access_token: TOKEN,
    access_expires_at: future(),
  });
  const input = {
    files: [new Blob(["%PDF-fake"], { type: "application/pdf" })],
    dataset: {
      technicalAssignmentId: FIRST,
      analysisDocumentId: SECOND,
      technicalAssignmentAccessRequired: "true",
    },
  };
  const body = new FormData();
  assert.equal(technicalAssignmentAccessError(input), null);
  appendTechnicalAssignmentPayload(body, input);
  assert.equal(body.get("technical_assignment_id"), FIRST);
  assert.equal(body.get("technical_assignment_analysis_document_id"), SECOND);
  assert.equal(body.get("technical_assignment_access_token"), TOKEN);
  forgetTechnicalAssignmentAccess(FIRST);
  assert.match(technicalAssignmentAccessError(input), /Загрузите файл повторно/);
});

test("просмотр ТЗ отправляет ключ заголовком и открывает только проверенный PDF", async () => {
  rememberPreparedTechnicalAssignment({
    technical_assignment_id: FIRST,
    access_token: TOKEN,
    access_expires_at: future(),
  });
  const previousFetch = globalThis.fetch;
  const previousCreate = URL.createObjectURL;
  const previousRevoke = URL.revokeObjectURL;
  let seen;
  let revoked;
  const tab = { opener: {}, location: { href: "" }, close() { throw new Error("PDF должен открыться"); } };
  const browser = {
    open: (url, target) => {
      assert.equal(url, "about:blank");
      assert.equal(target, "_blank");
      return tab;
    },
    setTimeout: (callback) => { callback(); },
  };
  globalThis.fetch = async (url, options) => {
    seen = { url, options };
    return new Response(new Blob(["%PDF-fake"], { type: "application/pdf" }), {
      headers: { "Content-Type": "application/pdf" },
    });
  };
  URL.createObjectURL = () => "blob:https://pdrd.example/preview";
  URL.revokeObjectURL = (url) => { revoked = url; };
  try {
    await openTechnicalAssignmentCitation(FIRST, 3, browser);
    assert.equal(seen.url, `/api/v1/normative/technical-assignments/${FIRST}/content`);
    assert.equal(seen.options.headers["X-PDRD-Technical-Assignment-Access"], TOKEN);
    assert.equal(seen.options.cache, "no-store");
    assert.equal(tab.opener, null);
    assert.equal(tab.location.href, "blob:https://pdrd.example/preview#page=3");
    assert.equal(revoked, "blob:https://pdrd.example/preview");
  } finally {
    globalThis.fetch = previousFetch;
    URL.createObjectURL = previousCreate;
    URL.revokeObjectURL = previousRevoke;
    forgetTechnicalAssignmentAccess(FIRST);
  }
});
