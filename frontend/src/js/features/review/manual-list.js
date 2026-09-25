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
  const items = new Map();

  function mount(root) {
    items.clear();
    section = root.querySelector(".analysis-result__findings");
    summary = null;
    overviewNote = null;
    overview = root.querySelector(".analysis-result__summary");
    automaticCount = section?.querySelectorAll(
      ".analysis-result__finding",
    ).length ?? 0;
  }

  function updateSummary() {
    if (overviewNote) {
      overviewNote.textContent = (
        `После ручной проверки: ${automaticCount + items.size} замечаний `
        + `(VLM: ${automaticCount}; Gold: ${items.size}).`
      );
    }
    if (summary) {
      summary.textContent = (
        `Текстовый список: ${automaticCount + items.size} замечаний `
        + `(${automaticCount} VLM, ${items.size} добавлено пользователем).`
      );
    }
  }

  function add(note, review) {
    if (!section) {
      return null;
    }
    if (items.has(note.findingId)) {
      throw new Error("Текстовая карточка этого замечания уже создана.");
    }

    if (!summary) {
      const empty = section.querySelector(".analysis-result__empty");
      if (empty) {
        empty.textContent = (
          "Автоматические замечания не сформированы. "
          + "Ниже приведены замечания пользователя."
        );
      }
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

    const item = { article, text, normative, decision };
    items.set(note.findingId, item);
    sync(review);
    updateSummary();
    return { article, textNode: text };
  }

  function sync(review) {
    const item = items.get(review.findingId);
    if (!item) {
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
  }

  return { mount, add, sync };
}