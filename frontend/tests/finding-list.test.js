// frontend/tests/finding-list.test.js

/** Независимые фильтры, стабильная сортировка и сохранение существующих контролов Review. */
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement } from "./helpers/fake-dom.js";
import { submitRejection } from "./helpers/rejection.js";
import { selectFindingEntries, mountFindingList } from "../src/js/features/analysis/finding-list.js";
import { renderAnalysisReport } from "../src/js/features/analysis/report.js";
import { createReviewController } from "../src/js/features/review/controller.js";

const entries = [
  { index: 0, category: "marking", severity: "error", status: "needs_review", decision: "pending", page: 4 },
  { index: 1, category: "equipment", severity: "warning", status: "confirmed", decision: "accepted", page: 1 },
  { index: 2, category: "marking", severity: "error", status: "confirmed", decision: "rejected", page: 2 },
  { index: 3, category: "other", severity: "info", status: "hypothesis", decision: "pending", page: 1 },
];

test("четыре фильтра комбинируются через И и не изменяют исходные записи", () => {
  const original = structuredClone(entries);
  for (const [key, value] of Object.entries({ category: "marking", severity: "error", status: "confirmed", decision: "rejected" })) {
    assert.ok(selectFindingEntries(entries, { [key]: value }).every((row) => row[key] === value));
  }
  assert.deepEqual(selectFindingEntries(entries, { category: "marking", severity: "error", decision: "pending" }).map((row) => row.index), [0]);
  assert.deepEqual(selectFindingEntries(entries, { status: "confirmed", decision: "pending" }), []);
  assert.equal(selectFindingEntries(entries, { status: "all", decision: "all" }).length, 4);
  assert.deepEqual(entries, original);
});

test("приоритет — pending, серьёзность, физическая страница, исходный порядок", () => {
  const extra = [
    { ...entries[0], page: 2, index: 4 }, { ...entries[0], page: 2, index: 5 },
    { ...entries[1], index: 6 },
  ];
  assert.deepEqual(selectFindingEntries([...entries, ...extra]).map((row) => row.index), [4, 5, 0, 3, 1, 6, 2]);
});

test("все поля сортируются в обе стороны; одинаковые значения сохраняют исходный порядок", () => {
  for (const sort of ["discovery", "page", "category", "severity", "status", "decision"]) {
    const forward = selectFindingEntries(entries, {}, sort, "asc");
    const backward = selectFindingEntries(entries, {}, sort, "desc");
    assert.equal(forward.length, backward.length);
    assert.notDeepEqual(forward.map((row) => row.index), backward.map((row) => row.index));
  }
  const equal = [{ ...entries[0], index: 8 }, { ...entries[0], index: 5 }];
  assert.deepEqual(selectFindingEntries(equal, {}, "page", "desc").map((row) => row.index), [5, 8]);
});

function environment() {
  const previous = globalThis.document;
  globalThis.document = { createElement: (tag) => new FakeElement(tag), createDocumentFragment: () => new FakeElement("fragment") };
  return () => { globalThis.document = previous; };
}

function collect(node) { return [node, ...node.children.flatMap(collect)]; }

test("панель сохраняет узлы и слушатели; пустой результат и сброс обновляют счётчик", () => {
  const restore = environment();
  try {
    const section = new FakeElement(); section.className = "analysis-result__findings";
    const cards = entries.map((row) => {
      const card = new FakeElement("article");
      card.dataset = { findingReportCard: "", findingDiscoveryIndex: String(row.index), findingCategory: row.category,
        findingSeverity: row.severity, findingStatus: row.status, reviewDecision: row.decision, reviewPage: String(row.page) };
      card.addEventListener("click", () => { card.clicked = true; }); section.append(card); return card;
    });
    const panel = mountFindingList(section);
    panel.selectors.category.value = "marking"; panel.selectors.category.dispatch("change");
    assert.equal(panel.counter.textContent, "Показано 2 из 4 замечаний");
    assert.equal(cards[1].hidden, true); cards[0].click(); assert.equal(cards[0].clicked, true);
    panel.selectors.status.value = "hypothesis"; panel.selectors.status.dispatch("change");
    assert.equal(panel.counter.textContent, "Показано 0 из 4 замечаний");
    collect(section).find((node) => node.className === "analysis-result__finding-reset").click();
    assert.equal(panel.counter.textContent, "Показано 4 из 4 замечаний");
    assert.ok(cards.every((card) => !card.hidden && card.listeners.get("click").length === 1));
  } finally { restore(); }
});

async function tick() { await new Promise((resolve) => queueMicrotask(resolve)); }

test("реальный Report и Review Controller: решения, фильтры, правка и восстановление работают вместе", async () => {
  const restore = environment();
  try {
    const findings = [
      { finding_id: "f1", page: 1, category: "marking", severity: "error", status: "needs_review", comment: "Первая находка" },
      { finding_id: "f2", page: 2, category: "equipment", severity: "warning", status: "confirmed", comment: "Вторая находка" },
      { finding_id: "h1", page: 2, category: "other", severity: "error", status: "hypothesis", comment: "Гипотеза" },
    ];
    const root = renderAnalysisReport({ status: "completed", source_mode: "pdf_only", findings, findings_count: 2 });
    const originals = structuredClone(findings);
    const controller = createReviewController(); controller.mount(root);
    const card = root.querySelectorAll("[data-finding-report-card]").find((node) => node.dataset.findingId === "f1");
    const filter = root.querySelectorAll("[data-finding-filter]").find((node) => node.dataset.findingFilter === "decision");
    filter.value = "pending"; filter.dispatch("change");
    const accept = card.querySelector(".review-controls__button--accept"); accept.click(); await tick();
    assert.equal(card.dataset.reviewDecision, "accepted"); assert.equal(card.hidden, true);
    assert.ok(card.classList.contains("analysis-result__finding--accepted"));
    assert.equal(card.dataset.findingStatus, "needs_review");
    filter.value = "all"; filter.dispatch("change");
    assert.equal(card.querySelector(".review-controls__button--accept"), accept);
    card.querySelector(".review-controls__button--reject").click(); submitRejection(root); await tick();
    assert.equal(card.hidden, false); assert.equal(card.classList.contains("is-hidden"), false);
    assert.ok(card.classList.contains("analysis-result__finding--rejected"));
    assert.ok(card.querySelector("[data-review-feedback]").textContent.length);
    card.querySelector(".review-controls__button--edit").click();
    const dialog = root.children.find((node) => node.className === "review-editor");
    dialog.children.find((node) => node.tagName === "TEXTAREA").value = "Уточнённый текст";
    dialog.children.find((node) => node.className === "review-editor__actions").children[1].click(); await tick();
    assert.equal(card.dataset.reviewDecision, "pending");
    assert.equal(card.querySelector("[data-review-text]").textContent, "Уточнённый текст");
    assert.ok(collect(card).some((node) => node.textContent === "Возможная ошибка"));
    const row = (id, text, decision) => ({ finding_id: id, origin: "vlm", original_text: text, text,
      original_basis: "", normative_basis: "", decision, revision: 2, proposed_regions: [],
      reason_category: decision === "rejected" ? "not_applicable" : null, comment: "" });
    controller.hydrate({ findings: [row("f1", "Первая находка", "rejected"), row("f2", "Вторая находка", "accepted")],
      decision_history: [{ finding_id: "f1", action: "decided", decision: "rejected", session_revision: 2, actor: "Инженер", comment: "Проверено вручную" }] });
    await tick();
    assert.equal(card.dataset.reviewDecision, "rejected"); assert.equal(card.hidden, false);
    assert.ok(collect(card.querySelector("[data-review-history]")).some((node) => node.textContent.includes("Проверено вручную")));
    assert.equal(controller.getReviewSnapshot().length, 2); // Гипотеза не добавлена в серверный Review.
    controller.setConnection({ isLocked: true }); accept.click();
    assert.equal(card.dataset.reviewDecision, "rejected");
    assert.deepEqual(findings, originals);
    controller.dispose();
  } finally { restore(); }
});
