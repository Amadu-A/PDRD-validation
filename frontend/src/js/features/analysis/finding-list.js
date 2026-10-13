// frontend/src/js/features/analysis/finding-list.js

/** Фильтры представления не меняют исходные находки и решения существующего Review. */
export const CATEGORY_LABELS = {
  normative_control: "Нормоконтроль", equipment: "Оборудование", scheme_logic: "Логика схемы",
  marking: "Маркировка", completeness: "Комплектность", optimization: "Оптимизация",
  customer_requirements: "Требования заказчика", document_consistency: "Согласованность документа", other: "Прочее",
};
export const SEVERITY_LABELS = { info: "Информация", warning: "Предупреждение", error: "Ошибка" };
export const CONFIRMATION_LABELS = { confirmed: "Подтверждено", needs_review: "Требует проверки инженером", hypothesis: "Гипотеза" };
export const DECISION_LABELS = { pending: "Ожидает решения инженера", accepted: "Принято инженером", rejected: "Отклонено" };
const refreshers = new WeakMap(), queued = new WeakSet();
let sequence = 0;

/** Стабильно сортирует копию; confirmed и accepted сравниваются по разным полям. */
export function selectFindingEntries(entries, filters = {}, sort = "pending_first", direction = "asc") {
  const selected = entries.filter((entry) => ["category", "severity", "status", "decision"]
    .every((key) => !filters[key] || filters[key] === "all" || entry[key] === filters[key]));
  const weights = {
    severity: { info: 0, warning: 1, error: 2 }, status: { confirmed: 0, needs_review: 1, hypothesis: 2 },
    decision: { pending: 0, accepted: 1, rejected: 2 },
  };
  const page = (entry) => Number.isInteger(Number(entry.page)) && Number(entry.page) > 0 ? Number(entry.page) : Number.MAX_SAFE_INTEGER;
  const compare = (left, right) => {
    if (sort === "pending_first") return (weights.decision[left.decision] ?? 0) - (weights.decision[right.decision] ?? 0)
      || (weights.severity[right.severity] ?? -1) - (weights.severity[left.severity] ?? -1)
      || page(left) - page(right);
    if (sort === "page") return page(left) - page(right);
    if (sort === "discovery") return left.index - right.index;
    if (sort === "category") return (CATEGORY_LABELS[left.category] || left.category || "")
      .localeCompare(CATEGORY_LABELS[right.category] || right.category || "", "ru");
    return (weights[sort]?.[left[sort]] ?? -1) - (weights[sort]?.[right[sort]] ?? -1);
  };
  return selected.sort((left, right) => (direction === "desc" ? -1 : 1) * compare(left, right) || left.index - right.index);
}

/** Сливает уведомления восстановления и редактирования в одну перерисовку списка. */
export function notifyFindingList(root) {
  for (const section of root?.querySelectorAll?.(".analysis-result__findings") || []) {
    if (!refreshers.has(section) || queued.has(section)) continue;
    queued.add(section);
    queueMicrotask(() => { queued.delete(section); refreshers.get(section)?.(); });
  }
}

function element(tag, text = null, className = "") {
  const node = document.createElement(tag); node.className = className;
  if (text !== null) node.textContent = String(text);
  return node;
}

/** Перемещает существующие карточки, сохраняя их DOM, контролы и подписки Review. */
export function mountFindingList(section) {
  sequence += 1;
  const panel = element("div", null, "analysis-result__finding-controls");
  const selectors = {};
  const options = {
    category: ["Категория", { all: "Все", ...CATEGORY_LABELS }],
    severity: ["Серьёзность", { all: "Все", ...SEVERITY_LABELS }],
    status: ["Подтверждение системой", { all: "Все", ...CONFIRMATION_LABELS }],
    decision: ["Решение человека", { all: "Все", pending: "Ожидает решения", accepted: "Принято", rejected: "Отклонено" }],
    sort: ["Сортировать по", { pending_first: "Сначала ожидающие решения", discovery: "Порядку обнаружения", page: "Номеру страницы", category: "Категории", severity: "Серьёзности", status: "Статусу подтверждения", decision: "Решению инженера" }],
    direction: ["Порядок", { asc: "Прямой", desc: "Обратный" }],
  };
  for (const [key, [label, values]] of Object.entries(options)) {
    const wrapper = element("label", label, "analysis-result__finding-control");
    const select = element("select"); select.id = `finding-filter-${sequence}-${key}`;
    select.dataset.findingFilter = key; wrapper.htmlFor = select.id;
    for (const [value, title] of Object.entries(values)) {
      const option = element("option", title); option.value = value; select.append(option);
    }
    select.value = Object.keys(values)[0]; selectors[key] = select;
    select.addEventListener("change", refresh); wrapper.append(select); panel.append(wrapper);
  }
  const reset = element("button", "Сбросить фильтры", "analysis-result__finding-reset"); reset.type = "button";
  reset.addEventListener("click", () => {
    for (const key of ["category", "severity", "status", "decision"]) selectors[key].value = "all";
    selectors.sort.value = "pending_first"; selectors.direction.value = "asc"; refresh();
  });
  panel.append(reset);
  const counter = element("p", "", "analysis-result__finding-counter"); counter.setAttribute("role", "status");
  const empty = element("p", "Нет замечаний, соответствующих выбранным фильтрам.", "analysis-result__empty");
  const list = element("div", null, "analysis-result__finding-list"); list.dataset.findingList = "";
  section.append(panel, counter, empty, list);
  let nextIndex = 0;
  function refresh() {
    const entries = [...section.querySelectorAll("[data-finding-report-card]")].map((node) => {
      if (!node.dataset.findingDiscoveryIndex) node.dataset.findingDiscoveryIndex = String(nextIndex);
      const index = Number(node.dataset.findingDiscoveryIndex); nextIndex = Math.max(nextIndex, index + 1);
      return { node, index, category: node.dataset.findingCategory || "other", severity: node.dataset.findingSeverity || "",
        status: node.dataset.findingStatus || "", decision: node.dataset.reviewDecision || "pending", page: node.dataset.reviewPage || node.dataset.manualPage };
    });
    const filters = Object.fromEntries(["category", "severity", "status", "decision"].map((key) => [key, selectors[key].value]));
    const visible = selectFindingEntries(entries, filters, selectors.sort.value, selectors.direction.value);
    const visibleNodes = new Set(visible.map((entry) => entry.node));
    for (const entry of entries) { entry.node.hidden = !visibleNodes.has(entry.node); }
    for (const entry of [...visible, ...entries.filter((item) => !visibleNodes.has(item.node))]) list.append(entry.node);
    counter.textContent = `Показано ${visible.length} из ${entries.length} замечаний`;
    empty.hidden = visible.length > 0;
  }
  refreshers.set(section, refresh); refresh();
  return { refresh, selectors, counter, list };
}
