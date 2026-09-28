// frontend/src/js/features/review/manual-list.js

/**
 * Текстовое представление пользовательских Gold-замечаний в основном отчёте.
 * Данные и решения передаются снаружи; здесь только безопасные DOM-узлы.
 */

function element(tagName, className, text = null) {
  const node = document.createElement(tagName);
  node.className = className;
  if (text !== null) {
    node.textContent = String(text);
  }
  return node;
}

function field(article, title, value) {
  const wrapper = element("div", "analysis-result__field");
  const label = element("strong", "analysis-result__field-label", title);
  const content = element("p", "analysis-result__field-value", value);
  wrapper.append(label, content);
  article.append(wrapper);
  return content;
}

/** Монтирует Gold-карточки непосредственно в существующем разделе замечаний. */
export function createManualTextList() {
  let section = null;
  let summary = null;
  let overview = null;
  let overviewNote = null;
  let automaticCount = 0;
  let automaticArticles = [];
  let empty = null;
  let emptyText = "";
  const items = new Map();

  function mount(root) {
    items.clear();
    section = root.querySelector(".analysis-result__findings");
    summary = null;
    overviewNote = null;
    overview = root.querySelector(".analysis-result__summary");
    automaticArticles = [...(section?.querySelectorAll(
      ".analysis-result__finding",
    ) ?? [])];
    automaticCount = automaticArticles.length;
    empty = section?.querySelector(".analysis-result__empty") ?? null;
    emptyText = empty?.textContent ?? "";
  }

  function updateSummary() {
    automaticCount = automaticArticles.filter((item) => item.dataset.reviewDecision !== "rejected").length;
    let number = automaticCount;
    let visibleCount = 0;
    for (const item of items.values()) {
      if (item.article.dataset.manualDecision === "rejected") continue;
      number += 1;
      visibleCount += 1;
      item.title.textContent = `${number}. Лист/страница ${item.pageNumber}`;
    }
    if (overviewNote) {
      overviewNote.textContent = (
        `В текстовом списке: ${automaticCount + visibleCount} замечаний `
        + `(VLM: ${automaticCount}; Gold: ${visibleCount}).`
      );
    }
    if (summary) {
      summary.textContent = (
        `Текстовый список: ${automaticCount + visibleCount} замечаний `
        + `(${automaticCount} VLM, ${visibleCount} добавлено пользователем).`
      );
    }
  }

  function ensureSummary() {
    if (!section || summary) return;
    summary = element("p", "manual-review-list__summary");
    summary.setAttribute("role", "status");
    summary.dataset.manualReviewSummary = "";
    section.append(summary);
    if (overview) {
      overviewNote = element("p", "manual-review-list__overview");
      overviewNote.setAttribute("role", "status");
      overview.append(overviewNote);
    }
  }

  function add(note, review) {
    if (!section) {
      return null;
    }
    if (items.has(note.findingId)) {
      throw new Error("Текстовая карточка этого замечания уже создана.");
    }

    ensureSummary();

    const number = automaticCount + items.size + 1;
    const article = element(
      "article",
      "analysis-result__finding manual-review-list__item",
    );
    article.dataset.manualFindingId = note.findingId;
    article.dataset.manualPage = String(note.pageNumber);

    const header = element("div", "analysis-result__finding-header");
    const title = element(
      "h4",
      "analysis-result__finding-title",
      `${number}. Лист/страница ${note.pageNumber}`,
    );
    const badges = element("div", "analysis-result__badges");
    const gold = element(
      "span",
      "analysis-result__badge manual-review-list__gold",
      "Gold · пользователь",
    );
    const decision = element(
      "span",
      "analysis-result__badge manual-review-list__decision",
    );
    badges.append(gold, decision);
    header.append(title, badges);
    article.append(header);

    field(article, "Категория", "Замечание пользователя");
    const text = field(article, "Замечание", review.text);
    field(article, "Основание на листе", "Область отмечена пользователем на листе.");
    const normative = field(article, "Нормативное основание", "Не указано");
    section.append(article);

    const item = { article, text, normative, decision, title, pageNumber: note.pageNumber };
    items.set(note.findingId, item);
    sync(review);
    updateSummary();
    return { article, textNode: text };
  }

  function sync(review) {
    ensureSummary();
    const item = items.get(review.findingId);
    if (!item) {
      updateSummary();
      return;
    }
    item.text.textContent = review.text;
    item.normative.textContent = review.normativeSection || "Не указано";
    item.article.dataset.manualDecision = review.decision;
    item.decision.textContent = {
      pending: "Ожидает решения",
      accepted: "Принято пользователем",
      rejected: "Отклонено пользователем",
    }[review.decision] ?? "Ожидает решения";
    item.article.classList.toggle("is-hidden", review.decision === "rejected");
    if (empty) empty.textContent = [...items.values()].some((entry) => entry.article.dataset.manualDecision !== "rejected")
      ? "Автоматические замечания не сформированы. Ниже приведены замечания пользователя."
      : emptyText;
    updateSummary();
  }

  /** Убирает Gold-карточку и пересчитывает номера и счётчики общего отчёта. */
  function remove(findingId) {
    const item = items.get(findingId);
    if (!item) return;
    item.article.remove();
    items.delete(findingId);
    if (!items.size && !automaticArticles.length) {
      summary?.remove();
      overviewNote?.remove();
      summary = null;
      overviewNote = null;
      if (empty) empty.textContent = emptyText;
    }
    updateSummary();
  }

  return { mount, add, sync, remove };
}
