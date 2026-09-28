// frontend/src/js/features/review/manual-note.js

/**
 * Отображение одного Gold-замечания на листе: рамка, соединитель и карточка.
 * Получает проверенную геометрию от контроллера выделения и не хранит решения.
 * Пользовательский текст вставляется только через textContent.
 */

import { connectorPoints, positionBox } from "./geometry.js";

/** Создаёт соединитель в нормализованных координатах изображения. */
export function createManualLine(issue, callout) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("manual-annotation__connector");
  svg.setAttribute("viewBox", "0 0 1000 1000");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", connectorPoints(issue, callout));
  svg.append(line);
  return svg;
}

/** Возвращает обновляемое представление без зависимости от Review state. */
export function createManualNote({
  findingId, pageNumber, issueBox, calloutBox, text, normativeSection,
}) {
  const region = document.createElement("div");
  region.className = "manual-annotation__issue";
  region.title = `Область пользовательского замечания на странице ${pageNumber}`;
  const line = createManualLine(issueBox, calloutBox);
  const card = document.createElement("article");
  card.className = "manual-annotation__card";
  card.dataset.findingId = findingId;
  card.dataset.manualPage = String(pageNumber);
  const textNode = document.createElement("p");
  textNode.className = "manual-annotation__text";
  const normNode = document.createElement("p");
  normNode.className = "manual-annotation__norm";
  card.append(textNode, normNode);

  const note = {
    findingId, pageNumber, region, line, card, textNode, normNode,
    issueBox: { ...issueBox },
    calloutBox: { ...calloutBox },

    applyGeometry(issue, callout) {
      note.issueBox = { ...issue };
      note.calloutBox = { ...callout };
      positionBox(region, issue);
      positionBox(card, callout);
      line.children[0].setAttribute("points", connectorPoints(issue, callout));
    },

    applyText(value, normative) {
      textNode.textContent = value;
      card.title = `${value}\n${normative || ""}`.trim();
      normNode.textContent = normative
        ? `Нормативное основание: ${normative}`
        : "Нормативное основание не указано";
    },

    remove() {
      region.remove();
      line.remove();
      card.remove();
    },
  };
  note.applyGeometry(issueBox, calloutBox);
  note.applyText(text, normativeSection);
  return note;
}
