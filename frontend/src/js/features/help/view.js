// frontend/src/js/features/help/view.js

/** Семантическое представление справки без интерпретации HTML из текста. */
/** Создаёт элемент, вставляя содержимое только как текст. */
export function helpElement(tag, text = null, className = "") {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== null) node.textContent = String(text);
  return node;
}

/** Отображает статью, пример, таблицу или нативный раскрывающийся ответ. */
export function renderGuideCard(item, { expanded = false } = {}) {
  const root = helpElement(item.collapsible ? "details" : "article", null, "help-center__card");
  root.id = item.id;
  root.tabIndex = -1;
  const heading = helpElement(item.collapsible ? "summary" : "h3", item.title);
  if (item.collapsible) root.open = expanded;
  if (item.source) root.dataset.source = item.source;
  root.append(heading);
  for (const text of item.paragraphs || []) root.append(helpElement("p", text));
  if (item.steps) {
    const list = helpElement("ol", null, "help-center__steps");
    for (const step of item.steps) list.append(helpElement("li", step));
    root.append(list);
  }
  if (item.facts) {
    const facts = helpElement("dl", null, "help-center__facts");
    for (const [label, value] of item.facts) {
      const pair = helpElement("div");
      pair.append(helpElement("dt", label), helpElement("dd", value));
      facts.append(pair);
    }
    root.append(facts);
  }
  if (item.example) root.append(helpElement("p", `Пример: ${item.example}`, "help-center__example"));
  if (item.table) {
    const scroll = helpElement("div", null, "help-center__table-scroll");
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", item.title);
    const table = helpElement("table", null, "help-center__table");
    table.append(helpElement("caption", item.title));
    const head = helpElement("thead"), row = helpElement("tr");
    for (const label of item.table.columns) {
      const cell = helpElement("th", label);
      cell.setAttribute("scope", "col");
      row.append(cell);
    }
    head.append(row); table.append(head);
    const body = helpElement("tbody");
    for (const values of item.table.rows) {
      const row = helpElement("tr");
      values.forEach((value, index) => {
        const cell = helpElement(index === 0 ? "th" : "td", value);
        if (index === 0) cell.setAttribute("scope", "row");
        row.append(cell);
      });
      body.append(row);
    }
    table.append(body); scroll.append(table); root.append(scroll);
  }
  return root;
}

/** Сценарий содержит инструкции, но не элементы управления настоящим анализом. */
export function scenarioCard(scenario) {
  return renderGuideCard({ id: `scenario-${scenario.id}`, title: scenario.title, facts: [
    ["Подготовьте", scenario.prepare], ["Раздел", scenario.section],
    ["Переключатели", scenario.toggles], ["Источники", scenario.sources],
    ["Вне проверки", scenario.outside], ["Время", scenario.duration],
  ] });
}
