// frontend/src/js/features/analysis/history.js

/** Собственная история в кабинете и на главной; ответы после выхода не показываются. */
import { subscribeSession } from "../auth/session.js";
import { downloadReviewedPdf } from "../review/download.js";
import { historyApi } from "./history-api.js";
import { statusLabel } from "./labels.js";

const reviewLabels = {
  not_opened: "Human Review: не открыт", in_progress: "Human Review: в работе",
  approved: "Human Review: утверждён", unavailable: "Human Review: статус временно недоступен",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Форматирует фактические числа; ещё неизвестные значения не подменяются нулём. */
export function historyDetails(item) {
  const details = [statusLabel(item.status)];
  if (Number.isInteger(item.pages_count)) details.push(`Страниц: ${item.pages_count}`);
  details.push(item.section_name || (item.section_id ? "Раздел удалён или недоступен" : "Без раздела"));
  if (Number.isInteger(item.findings_count)) details.push(`Замечаний: ${item.findings_count}`);
  if (item.status === "completed") details.push(reviewLabels[item.review_status] || reviewLabels.unavailable);
  if (item.status === "completed" && !item.result_available) details.push("Артефакты результата недоступны");
  return details.join(" · ");
}

/** Добавляет список и пагинацию к карточке; клиент и подписка заменяемы в тестах. */
export function createAnalysisHistory(root, {
  limit = 20, api = historyApi, subscribe = subscribeSession, download = downloadReviewedPdf,
} = {}) {
  const status = element("p", "analysis-history__status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const list = element("ul", "analysis-history__list");
  const actions = element("div", "analysis-history__actions");
  const previous = element("button", "analysis-history__button", "← Назад");
  const next = element("button", "analysis-history__button", "Далее →");
  const refresh = element("button", "analysis-history__button", "Обновить");
  for (const button of [previous, next, refresh]) button.type = "button";
  actions.append(previous, next, refresh);
  root.append(status, list, actions);
  let owner = null;
  let offset = 0;
  let sequence = 0;
  let busy = false;
  let hasMore = false;

  function controls() {
    previous.disabled = busy || offset === 0;
    next.disabled = busy || !hasMore;
    refresh.disabled = busy || !owner;
  }

  function row(item, request) {
    const li = element("li", "analysis-history__item");
    const date = new Date(item.created_at);
    const heading = element("p", "analysis-history__heading", `${Number.isNaN(date.getTime()) ? "" : date.toLocaleString("ru-RU")} · ${item.file_name || "Документ"}`);
    const details = element("p", "analysis-history__details", historyDetails(item));
    const links = element("div", "analysis-history__actions");
    const open = element("a", "analysis-history__button", "Открыть");
    open.href = `/?job_id=${encodeURIComponent(item.job_id)}`;
    links.append(open);
    if (item.pdf_url) {
      const pdf = element("button", "analysis-history__button", item.pdf_kind === "reviewed" ? "Итоговый PDF" : "PDF с автозамечаниями");
      pdf.type = "button";
      pdf.addEventListener("click", async () => {
        pdf.disabled = true;
        status.textContent = "Формируем PDF…";
        try {
          const result = await api.pdf(item);
          if (sequence === request && owner) { download(result); status.textContent = "PDF готов."; }
        } catch (error) {
          if (sequence === request) status.textContent = error.detail ?? error.message;
        } finally { if (sequence === request) pdf.disabled = false; }
      });
      links.append(pdf);
    }
    li.append(heading, details, links);
    return li;
  }

  async function load() {
    if (!owner) return;
    const request = ++sequence;
    busy = true;
    controls();
    status.textContent = "Загружаем историю проверок…";
    list.replaceChildren();
    try {
      const result = await api.list({ limit, offset });
      if (request !== sequence || !owner) return;
      hasMore = result.has_more === true;
      list.replaceChildren(...result.items.map((item) => row(item, request)));
      status.textContent = result.items.length ? "" : "У вас пока нет сохранённых проверок.";
    } catch (error) {
      if (request !== sequence) return;
      hasMore = false;
      status.textContent = error.detail ?? "Не удалось загрузить историю. Нажмите «Обновить».";
    } finally {
      if (request === sequence) { busy = false; controls(); }
    }
  }
  previous.addEventListener("click", () => { if (!busy && offset > 0) { offset = Math.max(0, offset - limit); void load(); } });
  next.addEventListener("click", () => { if (!busy && hasMore) { offset += limit; void load(); } });
  refresh.addEventListener("click", () => { offset = 0; void load(); });
  const unsubscribe = subscribe((session) => {
    const nextOwner = session.authenticated ? session.user?.user_id ?? session.user?.id : null;
    root.hidden = !nextOwner;
    if (nextOwner === owner) return;
    sequence += 1;
    owner = nextOwner;
    offset = 0;
    busy = false;
    hasMore = false;
    list.replaceChildren();
    status.textContent = "";
    controls();
    if (owner) void load();
  });
  controls();
  return { refresh: load, dispose() { owner = null; sequence += 1; unsubscribe(); list.replaceChildren(); } };
}

/** На широком экране история стоит под Experience, у проектировщика занимает его место. */
export function mountAnalysisHistoryNavigation(page) {
  const auxiliary = element("aside", "page__auxiliary");
  auxiliary.hidden = true;
  const experience = page.querySelector("[data-experience-nav]");
  if (experience) auxiliary.append(experience);
  const panel = element("section", "analysis-history analysis-history--compact");
  panel.append(element("h2", "analysis-history__title", "История проверок"));
  const link = element("a", "analysis-history__all", "Вся история в личном кабинете →");
  link.href = "/account.html#history";
  panel.append(link);
  auxiliary.append(panel);
  page.append(auxiliary);
  subscribeSession((session) => { auxiliary.hidden = !session.authenticated; });
  return createAnalysisHistory(panel, { limit: 5 });
}
