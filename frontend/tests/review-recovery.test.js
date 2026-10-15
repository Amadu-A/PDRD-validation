// frontend/tests/review-recovery.test.js

/** Настоящий рендер, редактор, очередь CAS и PDF после изменения состава рамок визуализации. */

import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { submitRejection } from "./helpers/rejection.js";
import { renderAnalysisReport } from "../src/js/features/analysis/report.js";
import { appendAnnotatedPdfDownload } from "../src/js/features/analysis/pdf-export.js";
import { getVisualizationReview, reviewCalloutBox } from "../src/js/features/analysis/visualization-review.js";
import { createReviewPersistence } from "../src/js/features/review/persistence.js";

const JOB = "44444444-4444-4444-8444-444444444444";
const box = (i) => ({ x_min: 100 + i * 60, y_min: 200, x_max: 140 + i * 60, y_max: 260 });
const settle = () => new Promise(setImmediate);
const action = (node, name) => node.querySelectorAll("[data-review-action]").find((button) => button.dataset.reviewAction === name);
const resize = (node, direction, key = "ArrowRight") => node.querySelectorAll("[data-resize-direction]")
  .find((button) => button.dataset.resizeDirection === direction)
  .dispatch("keydown", { key, preventDefault() {}, stopPropagation() {} });

function setup(t, freshCounts, savedCounts, grouped = false) {
  const previous = { document: globalThis.document, window: globalThis.window, fetch: globalThis.fetch };
  const downloads = [];
  const root = new FakeElement();
  globalThis.document = {
    body: new FakeElement("body"),
    createElement: (tag) => {
      const element = new FakeElement(tag);
      if (tag === "a") element.click = () => { if (element.download) downloads.push(element.download); };
      return element;
    },
    createElementNS: (_namespace, tag) => new FakeElement(tag),
    createDocumentFragment: () => new FakeElement("fragment"),
    createTextNode: (text) => Object.assign(new FakeElement("text"), { textContent: text }),
  };
  globalThis.window = { location: { href: `https://pdrd.test/?job_id=${JOB}` }, history: { replaceState() {} },
    setTimeout: (callback) => callback(), requestAnimationFrame() {}, addEventListener() {}, removeEventListener() {} };
  globalThis.fetch = async () => new Response(JSON.stringify({ eligible: 4, excluded: 0, created: 4 }));
  let persistence;
  t.after(() => { persistence?.clear(); Object.assign(globalThis, previous); });
  const findings = freshCounts.map((count, i) => ({
    finding_id: `vlm:${i}`, page: 1, comment: `Исходное замечание ${i}`, status: "needs_review", severity: "warning",
    category: "scheme_logic", object_ref: grouped ? "КЛ-12" : `КЛ-${i}`,
    location: { status: count ? "located" : "unlocated", regions: Array.from({ length: count }, (_, j) => ({ bbox: box(j), confidence: 0.9, source: "vlm" })) },
  }));
  const payload = { status: "completed", source_mode: "pdf_only", findings };
  const visualization = { pages: [{ page_number: 1, image_base64: "image", locations: [] }] };
  root.append(renderAnalysisReport(payload, { jobId: JOB, visualization }));
  appendAnnotatedPdfDownload(root, { jobId: JOB, payload });
  for (const image of root.querySelectorAll(".analysis-result__page-image")) { image.complete = true; image.naturalWidth = 1000; }
  const page = root.querySelector(".analysis-result__page-visualization");
  const adapter = getVisualizationReview(page);
  let server = { job_id: JOB, revision: 7, approved_revision: null, findings: savedCounts.map((count, i) => ({
    finding_id: `vlm:${i}`, origin: "vlm", page_number: 1, original_text: `Исходное замечание ${i}`, original_basis: "",
    text: `Сохранённое замечание ${i}`, normative_basis: "", revision: 2, decision: "pending",
    proposed_regions: Array.from({ length: count }, (_, j) => ({ bbox: box(j), confidence: 0.9, source: "vlm", method: "analysis_vlm" })),
    display_regions: null, callout_box: null, evidence_locations: [],
  })) };
  const copy = () => structuredClone({ ...server, pending_count: server.findings.filter((row) => row.decision === "pending").length });
  const commands = [];
  const pdfRequests = [];
  const api = {
    config: async () => ({ enabled: true }), open: async () => copy(),
    command: async (_job, command) => {
      assert.equal(command.expected_revision, server.revision);
      commands.push(structuredClone(command));
      const row = server.findings.find((item) => item.finding_id === command.finding_id);
      if (command.action === "add") server.findings.push({ ...command, origin: "manual", original_text: command.text,
        original_basis: command.normative_basis, normative_basis: command.normative_basis, decision: "pending", revision: 0 });
      else if (command.action === "edit") Object.assign(row, { text: command.text, normative_basis: command.normative_basis, decision: "pending" });
      else if (command.action === "geometry") {
        if (row.origin === "manual") row.issue_box = command.regions[0];
        else row.display_regions = command.regions;
        Object.assign(row, { callout_box: command.callout_box, decision: "pending" });
      } else if (command.action === "decide") Object.assign(row, {
        decision: command.decision, reason_category: command.reason_category ?? null, comment: command.comment ?? "",
      });
      else if (command.action === "reset") row.decision = "pending";
      else if (command.action !== "approve") assert.fail(`Неожиданная команда ${command.action}`);
      server.revision += 1;
      server.approved_revision = command.action === "approve" ? server.revision : null;
      if (command.action === "approve") {
        assert.equal(copy().pending_count, 0);
        server.area_confirmations = server.findings.filter((item) => item.decision === "accepted")
          .map((item) => ({ finding_id: item.finding_id, valid: true }));
      }
      return copy();
    },
    pdf: async (job, revision) => {
      assert.equal(job, JOB); assert.equal(revision, server.approved_revision);
      pdfRequests.push(revision);
      return { blob: new Blob(["%PDF-1.7\nreviewed"]), filename: "Итоговый.pdf" };
    },
  };
  persistence = createReviewPersistence({ api });
  persistence.mount(root, { jobId: JOB });
  return { root, page, adapter, commands, pdfRequests, downloads, persistence, server: () => copy() };
}

for (const [fresh, saved] of [[2, 0], [0, 2], [1, 3], [5, 4], [1, 1]]) {
  test(`восстановление ${fresh} → ${saved} рамок разблокирует Review без фиктивного сохранения`, async (t) => {
    const f = setup(t, [fresh], [saved]);
    await settle();
    const record = f.adapter.records[0];
    assert.equal(record.bboxEntries.length, saved);
    assert.equal(record.connectorEntries.length, saved);
    assert.equal(f.adapter.groups[0].bboxEntries.length, saved);
    assert.equal(f.adapter.imagePane.querySelectorAll(".analysis-result__bbox").length, saved);
    assert.equal(action(record.item, "accept").disabled, false);
    assert.match(f.root.querySelector("[data-review-preview]").textContent, /сохранены на сервере/);
    assert.equal(f.commands.length, 0);
    assert.equal(f.root.querySelectorAll(".review-sync").length, 0);
    assert.equal(record.item.querySelector(".analysis-result__annotation-title").textContent, "Сохранённое замечание 0");
    if (saved) {
      resize(record.bboxEntries[0].node, "e"); await settle();
      assert.equal(f.commands[0].action, "geometry");
      assert.equal(f.commands[0].regions.length, saved);
      assert.equal(f.commands[0].regions[0].x_max, box(0).x_max + 5);
    }
    action(record.item, "accept").click(); await settle();
    assert.equal(f.server().findings[0].decision, "accepted");
  });
}

for (const grouped of [false, true]) {
  test(`полный Review после смены рамок: текст, размеры, Gold, отклонение и итоговый PDF; группа=${grouped}`, async (t) => {
    const f = setup(t, [1, 2, 4, 1], [2, 0, 1, 1], grouped);
    await settle();
    const record = f.adapter.records[0];
    resize(record.bboxEntries[0].node, "e"); await settle();
    resize(record.callout, "w"); await settle();
    assert.ok(reviewCalloutBox(record.callout));
    assert.ok(f.commands.some((command) => command.action === "geometry" && command.callout_box));
    action(record.item, "edit").click();
    const dialog = f.root.querySelector(".review-editor");
    dialog.children.find((child) => child.tagName === "TEXTAREA").value = "Исправленное инженером замечание";
    dialog.querySelector("[data-review-save]").click(); await settle();
    assert.equal(f.server().findings[0].text, "Исправленное инженером замечание");

    f.root.querySelector("[data-review-page-add]").click();
    const layer = f.root.querySelector(".manual-annotation__layer");
    const point = (x, y, pointerId) => ({ button: 0, clientX: x, clientY: y, pointerId, preventDefault() {} });
    layer.dispatch("pointerdown", point(100, 100, 1)); layer.dispatch("pointerup", point(200, 180, 1));
    layer.dispatch("pointerdown", point(300, 250, 2)); layer.dispatch("pointerup", point(500, 350, 2));
    const manualDialog = f.root.querySelector(".manual-editor");
    assert.equal(manualDialog.open, true);
    const form = manualDialog.children[0];
    form.children.find((child) => child.tagName === "TEXTAREA").value = "Замечание пользователя";
    form.children.find((child) => child.tagName === "INPUT").value = "Основание пользователя";
    form.dispatch("submit", { preventDefault() {} }); await settle();
    const gold = f.root.querySelector(".manual-annotation__card");
    assert.ok(gold);
    action(gold, "edit").click();
    form.children.find((child) => child.tagName === "TEXTAREA").value = "Исправленное Gold";
    form.dispatch("submit", { preventDefault() {} }); await settle();
    assert.equal(f.server().findings.find((row) => row.origin === "manual").text, "Исправленное Gold");
    action(gold, "reject").click(); submitRejection(f.root, "other", "Удалено пользователем"); await settle();
    assert.equal(gold.classList.contains("is-hidden"), true);
    assert.equal(f.server().findings.find((row) => row.origin === "manual").decision, "rejected");
    for (const row of f.adapter.records) { action(row.item, "accept").click(); await settle(); }
    const button = f.root.querySelector("[data-analysis-pdf-reviewed]");
    assert.equal(button.disabled, false);
    button.click(); await settle(); await settle();
    assert.equal(f.pdfRequests.length, 1);
    assert.deepEqual(f.downloads, ["Итоговый.pdf"]);
    assert.equal(f.commands.filter((command) => command.action === "approve").length, 1);
    const snapshot = f.server();
    f.persistence.mount(f.root, { jobId: JOB }); await settle();
    assert.equal(f.root.querySelectorAll(".manual-annotation__card").length, 1);
    assert.equal(f.root.querySelectorAll(".manual-editor").length, 1);
    assert.equal(f.server().revision, snapshot.revision);
    assert.equal(f.root.querySelector(".manual-annotation__card").classList.contains("is-hidden"), true);
    assert.equal(f.adapter.records[0].item.querySelector(".analysis-result__annotation-title").textContent, "Исправленное инженером замечание");
  });
}
