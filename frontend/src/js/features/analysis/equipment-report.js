// frontend/src/js/features/analysis/equipment-report.js

/** Таблица EQ отделяет технические отказы поиска от инженерных замечаний. */
import { followEquipmentEvents } from "./equipment-events.js";
import { guestAccessHeaders } from "./guest-access.js";
import { equipmentSnapshotUrl } from "./finding-provenance.js";
import { reviewCapabilities } from "../review/access.js";

export const EQUIPMENT_STATUS_LABELS = {
  confirmed: "Подтверждено", mismatch: "Несоответствие", incomplete: "Частично выполнено",
  not_checked: "Не проверено", needs_identification: "Нужна идентификация",
  no_data: "Нет данных для сравнения",
};
export const EQUIPMENT_REASON_LABELS = {
  network_error: "Ошибка сети", dns_error: "Ошибка DNS", tls_error: "Ошибка TLS",
  http_error: "Документ недоступен по HTTP", url_blocked: "Адрес запрещён",
  redirect_blocked: "Перенаправление запрещено", file_too_large: "Превышен размер файла",
  unsupported_file: "Неподдерживаемый формат", manufacturer_mismatch: "Другой производитель",
  manufacturer_unknown: "Производитель не подтверждён", model_mismatch: "Другая модель",
  link_model_mismatch: "Ссылка пропущена: явно другая модель; попытка загрузки не потрачена",
  variant_mismatch: "Исполнение не подтверждено", extraction_error: "Ошибка извлечения",
  article_mismatch: "Артикул не подтверждён", operation_interrupted: "Попытка прервана перезапуском",
  project_properties_missing: "В проекте нет подтверждённых характеристик для сравнения",
  revision_ambiguous: "Несколько редакций документа; нужна проверка применимости",
  facts_unavailable: "Недостаточно сравнимых характеристик", query_budget_exhausted: "Бюджет поиска исчерпан",
  download_budget_exhausted: "Лимит попыток загрузки исчерпан", vision_budget_exhausted: "Лимит визуального чтения исчерпан",
  source_unverified: "Источник не подтверждён", source_blocked: "Источник заблокирован",
  document_not_found: "Документ не найден", not_checked: "Поиск не выполнялся",
  model_uncertain: "Маркировка требует уточнения", operation_timeout: "Время попытки истекло",
  cancelled: "Отменено", internal_error: "Ошибка обработки", scan_requires_vision: "Нужен адресный VLM",
};

/** Попытки загрузки не выдаются за документы, прошедшие проверку применимости. */
export function equipmentReason(row) {
  const metrics = row.metrics || {};
  const used = metrics.downloads_in_run;
  const limit = metrics.download_limit_per_run;
  const reason = EQUIPMENT_REASON_LABELS[row.reason_code] || row.reason || "";
  const exhausted = Number.isInteger(used) && Number.isInteger(limit) && limit > 0 && used >= limit;
  if (row.reason_code !== "download_budget_exhausted"
      && !(exhausted && !row.document?.source_id && ["incomplete", "not_checked"].includes(row.status))) return reason;
  const count = Number.isInteger(used) && Number.isInteger(limit)
    ? `Использованы ${used} из ${limit} разрешённых попыток загрузки в этом запуске.`
    : `Попыток загрузки: ${metrics.downloads || 0}. Лимит исчерпан.`;
  const cause = row.reason_code === "download_budget_exhausted" ? "" : `${reason}. `;
  return `${cause}${count} Подходящая документация не подтверждена. Можно продолжить поиск, если доступны продолжения.`;
}

function element(tag, text = null, className = "") {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== null) node.textContent = String(text);
  return node;
}

/** Старые отчёты отображаются без придуманных результатов поиска. */
export function equipmentModels(check) {
  if (Array.isArray(check.models) && check.models.length) return check.models;
  return (check.inventory || []).map((item) => ({ ...item,
    status: item.status === "resolved" ? "not_checked" : "needs_identification",
    reason_code: "not_checked", metrics: {}, document: null,
    pages: [...new Set((item.occurrences || []).map((part) => part.page))],
  }));
}

export function filterEquipmentModels(models, filter) {
  return filter === "all" ? models : models.filter((row) => row.status === filter);
}

/** Добавляет безопасный DOM и возвращает остановку фоновых запросов. */
export function appendEquipmentReport(parent, initial, {
  jobId = null, fetchImpl = globalThis.fetch, followImpl = followEquipmentEvents,
  autoUpdates = true,
} = {}) {
  const root = element("section", null, "equipment-report");
  const title = element("h3", "Проверка оборудования", "equipment-report__title");
  const summary = element("div", null, "equipment-report__summary");
  const message = element("p", "", "equipment-report__message");
  message.setAttribute("role", "status");
  const controls = element("div", null, "equipment-report__controls");
  const filters = element("div", null, "equipment-report__filters");
  const scroll = element("div", null, "equipment-report__scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", "Таблица оборудования; прокрутите вправо для всех столбцов");
  root.append(title, summary, message, controls, filters, scroll);
  parent.append(root);
  let check = initial, search = { status: initial.search_status, revision: initial.revision };
  let filter = "all", proposal = null, disposed = false, refreshPromise = null, timer = null;
  let stream = null, observer = null, wasConnected = false;
  const abort = new AbortController();
  const base = `/api/v1/analyses/${encodeURIComponent(jobId)}`;

  async function request(suffix, body) {
    const response = await fetchImpl(base + suffix, {
      method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
      signal: abort.signal, headers: { ...guestAccessHeaders(jobId), Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new Error(response.status === 409
      ? "Версия изменилась или Review уже утверждён. Обновите данные."
      : "EQ операция недоступна. Проверьте доступ и состояние сервисов.");
    return response.json();
  }

  async function snapshot(sourceId) {
    const url = equipmentSnapshotUrl(jobId, sourceId);
    if (!url) return;
    try {
      const response = await fetchImpl(url, { headers: guestAccessHeaders(jobId), credentials: "same-origin", signal: abort.signal });
      if (!response.ok) throw new Error("Сохранённый документ недоступен.");
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const link = element("a");
      link.href = href;
      link.download = `${sourceId}.${blob.type.includes("pdf") ? "pdf" : "html"}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (error) { if (!disposed) message.textContent = error.message; }
  }

  function action(label, handler) {
    const button = element("button", label, "equipment-report__action");
    button.type = "button";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await handler(); } catch (error) { if (!disposed) message.textContent = error.message; }
      finally { button.disabled = false; }
    });
    controls.append(button);
    return button;
  }

  function paint() {
    const models = equipmentModels(check);
    summary.replaceChildren(); filters.replaceChildren(); controls.replaceChildren();
    const stats = check.statistics || {};
    for (const [label, value] of [
      ["Распознано записей", stats.recognized_records ?? models.reduce((n, row) => n + (row.occurrences?.length || 1), 0)],
      ["Уникальных моделей", stats.unique_models ?? models.length],
      ["Отправлено на поиск", stats.searched_models ?? models.filter((row) => row.metrics?.queries > 0).length],
      ["Сохранено документов", stats.saved_documents ?? models.filter((row) => row.document).length],
    ]) {
      const card = element("div", null, "equipment-report__stat");
      card.append(element("span", label), element("strong", value)); summary.append(card);
    }
    for (const [key, label] of Object.entries({ all: "Все", ...EQUIPMENT_STATUS_LABELS })) {
      const count = filterEquipmentModels(models, key).length;
      const button = element("button", `${label} ${count}`, "equipment-report__filter");
      button.type = "button"; button.setAttribute("aria-pressed", String(filter === key));
      button.addEventListener("click", () => { filter = key; paint(); }); filters.append(button);
    }
    const table = element("table", null, "equipment-report__table");
    table.append(element("caption", "По каждой модели: результат, причины, попытки загрузки и сохранённый документ. Таблицу можно прокрутить вправо."));
    const head = element("thead"), heading = element("tr");
    for (const label of ["№", "Производитель", "Модель", "Исполнение", "Результат", "Причина / доказательства", "Запросы", "Попытки загрузки", "Документ"]) {
      const cell = element("th", label); cell.setAttribute("scope", "col"); heading.append(cell);
    }
    head.append(heading); table.append(head);
    const body = element("tbody");
    for (const row of filterEquipmentModels(models, filter)) {
      const tr = element("tr");
      const modelName = [row.model, ["model_name", "interface"].includes(row.variant_kind) ? row.variant : ""].filter(Boolean).join(" ");
      for (const value of [models.indexOf(row) + 1, row.manufacturer || "Требует уточнения", modelName,
        row.variant_kind === "execution" || !row.variant_kind ? row.variant || "—" : "—"]) tr.append(element("td", value));
      const status = element("td");
      status.append(element("span", EQUIPMENT_STATUS_LABELS[row.status] || "Частично выполнено", `equipment-report__status equipment-report__status--${EQUIPMENT_STATUS_LABELS[row.status] ? row.status : "incomplete"}`));
      tr.append(status);
      const reason = element("td");
      reason.append(element("p", equipmentReason(row)));
      const details = element("details"); details.append(element("summary", "Подробнее"));
      if (row.article) details.append(element("p", `Артикул: ${row.article}`));
      details.append(element("p", `Листы: ${(row.pages || []).join(", ") || "—"}. Визуальных чтений: ${row.metrics?.vision_calls || 0}. Документов, прошедших проверку применимости: ${row.metrics?.verified_documents || 0}.`));
      for (const attempt of row.attempts || []) details.append(element("p", `${attempt.hostname || ""}: ${EQUIPMENT_REASON_LABELS[attempt.code] || (attempt.code === "verified" ? "Документ проверен" : "Обработка не завершена")}`));
      for (const comparison of row.comparisons || []) details.append(element("p", `Документ, стр. ${comparison.source_page}: ${comparison.manufacturer_evidence || ""}`));
      reason.append(details); tr.append(reason);
      tr.append(element("td", row.metrics?.queries || 0), element("td", row.metrics?.downloads || 0));
      const documentCell = element("td");
      if (row.document?.source_id && jobId) {
        const download = element("button", "Сохранённый документ", "equipment-report__action");
        download.type = "button"; download.addEventListener("click", () => snapshot(row.document.source_id));
        documentCell.append(download, element("small", `Редакция: ${row.document.revision || "не указана"}; ${row.document.trust_status === "trusted" ? "источник подтверждён" : "неподтверждённый источник"}`));
      } else documentCell.textContent = "Нет документа";
      tr.append(documentCell); body.append(tr);
    }
    table.append(body); scroll.replaceChildren(table);
    message.textContent = "";
    if (!jobId) return;
    if (["queued", "running"].includes(search.status)) {
      message.textContent = "EQ продолжается в фоне. Основной анализ завершён; результаты других источников доступны.";
      action("Отменить EQ", async () => { search = await request("/equipment-search/cancel", {}); await refresh(); });
    } else if (["incomplete", "cancelled"].includes(search.status)
        && (search.resume_count || 0) < (search.policy?.max_resume_runs ?? 3)) {
      action("Продолжить проверку", async () => {
        search = await request("/equipment-search/resume", { revision: search.revision });
        await refresh(); follow();
      });
    }
    if (proposal && reviewCapabilities().canDecide && !["queued", "running"].includes(search.status) && proposal.findings?.length) {
      action("Добавить EQ-находки в Review", async () => {
        const review = await request("/review/open", {});
        await request("/review/equipment-updates", { revision: proposal.revision, expected_revision: review.revision });
        message.textContent = "EQ-находки добавлены для рассмотрения. Обновите Human Review для их просмотра.";
      });
    }
  }

  async function refresh() {
    if (disposed) return;
    if (refreshPromise) return refreshPromise;
    refreshPromise = (async () => {
      try {
        const latest = await request("/equipment-search/report");
        if (disposed || Number(latest.revision) < Number(search.revision || 0)) return;
        proposal = latest; check = latest.report; search = latest.search;
        paint();
      } catch (error) { if (!disposed) message.textContent = error.message; }
      finally { refreshPromise = null; }
    })();
    return refreshPromise;
  }

  function follow() {
    stream?.abort(); stream = new AbortController();
    if (!["queued", "running"].includes(search.status) || disposed) return;
    void followImpl(jobId, { signal: stream.signal, afterEventId: search.event_sequence || 0,
      onEvent: (event) => {
        const terminal = ["completed", "incomplete", "cancelled"].includes(event.type);
        if (terminal) clearTimeout(timer);
        if (timer === null || terminal) timer = setTimeout(() => { timer = null; void refresh(); }, terminal ? 0 : 1000);
      }, fetchImpl });
  }

  function dispose() { disposed = true; abort.abort(); stream?.abort(); clearTimeout(timer); observer?.disconnect(); }
  paint();
  if (autoUpdates && jobId) {
    queueMicrotask(async () => { wasConnected = Boolean(root.isConnected); await refresh(); if (!disposed) follow(); });
    if (globalThis.MutationObserver && document.documentElement) {
      observer = new MutationObserver(() => {
        if (root.isConnected) wasConnected = true;
        else if (wasConnected) dispose();
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    }
  }
  return { root, refresh, dispose };
}
