// frontend/src/js/features/analysis/equipment-report.js

/** Таблица EQ отделяет технические отказы поиска от инженерных замечаний. */
import { followEquipmentEvents } from "./equipment-events.js";
import { guestAccessHeaders } from "./guest-access.js";
import { equipmentSnapshotUrl, focusPageEvidence, safeExternalSourceUrl } from "./finding-provenance.js";
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

/** Использует проектное наименование и маркировку, не дополняя их догадками о бренде. */
export function equipmentDisplayName(row) {
  if (typeof row.specification_caption === "string" && row.specification_caption) return row.specification_caption;
  const description = (row.occurrences || []).find((item) => item.equipment_type)?.equipment_type || row.name || "";
  const marking = [row.model, row.variant].filter(Boolean).join(
    row.variant_kind === "execution" || !row.variant_kind ? ", " : " "
  );
  const manufacturer = row.identity ? row.identity.manufacturer : row.manufacturer;
  return [...new Set([description, marking, manufacturer].filter(Boolean).map(String))].join(" · ") || "Наименование требует уточнения";
}

/** Показывает каждую позицию спецификации, сохраняя один общий поиск по модели. */
export function equipmentRows(check) {
  const models = equipmentModels(check);
  const positions = check.specification?.structured_items;
  if (!Array.isArray(positions) || !positions.length) return models;
  const byRow = new Map();
  for (const model of models) for (const part of model.occurrences || []) {
    for (const id of part.source_row_ids || []) byRow.set(id, model);
  }
  const rows = positions.map((item) => {
    const ids = new Set(item.row_ids || []);
    const base = [...ids].map((id) => byRow.get(id)).find(Boolean) || {};
    const occurrences = (base.occurrences || []).filter((part) => (part.source_row_ids || []).some((id) => ids.has(id)));
    const comparisons = (base.comparisons || []).filter((part) => (item.source_pages || []).includes(part.page) && part.object_ref === (item.position || item.item_id));
    const statuses = new Set(comparisons.map((part) => part.status));
    let status = base.status || "not_checked";
    if (base.document?.source_id && !base.document.revision_ambiguous) {
      status = statuses.has("mismatch_candidate") ? "mismatch"
        : comparisons.length && statuses.size === 1 && statuses.has("matched") ? "confirmed"
          : "no_data";
    }
    return { ...base, equipment_id: item.item_id, specification_caption: item.Caption, status, comparisons,
      manufacturer: item.manufacturer || "", model: item.model || "", variant: item.variant || "", article: item.article || "",
      identity: { ...(base.identity || {}), manufacturer: item.manufacturer || "" },
      pages: item.source_pages || [], quantity: item.quantity, units: item.units, project_position: item.position,
      occurrences: occurrences.length ? occurrences : (item.source_pages || []).map((page) => ({ page,
        equipment_type: item.name || "", visual_regions: (item.source_regions || []).filter((region) => region.page === page),
      })), metrics: base.metrics || {},
    };
  });
  // Вхождения на обычных чертежах также остаются доступны, без привязки к позиции по догадке.
  const drawingRows = models.flatMap((model) => {
    const occurrences = (model.occurrences || []).filter((part) => !(part.source_row_ids || []).length);
    return occurrences.length ? [{ ...model, occurrences, pages: [...new Set(occurrences.map((part) => part.page))] }] : [];
  });
  return [...rows, ...drawingRows];
}

/** Ключ не зависит от позиции строки, фильтра или уточнённого в поиске производителя. */
export function equipmentRowId(row) {
  const identity = row.identity || row;
  return String(row.equipment_id || JSON.stringify([
    identity.manufacturer || "", row.model || "", row.variant || "", row.article || "",
    row.model ? "" : (row.occurrences || []).map((item) => `${item.page}:${item.object_ref || ""}`).sort().join("|"),
  ]));
}

/** Документ означает сохранённый прочитанный snapshot, а не найденную поисковую ссылку. */
export function equipmentDocumentStatus(row) {
  if (row.manufacturer_site?.relationship === "analog" || row.document?.applicability === "analog") return "Недостаточно данных";
  if (!row.document?.source_id) return "Не проверено";
  if (row.document.revision_ambiguous || row.reason_code === "revision_ambiguous") return "Требуется уточнение редакции";
  if (row.status === "confirmed") return "Соответствует";
  if (row.status === "mismatch") return "Есть замечание";
  return "Недостаточно данных";
}

export function equipmentSourceTrustLabel(document) {
  /** Описывает историческое разрешение сайта, не обещая применимость документа. */
  if (document.trust_status === "trusted") return "сайт был разрешён для поиска при загрузке";
  if (document.trust_status === "unverified_user_override") return "неподтверждённый источник — разрешён только для этого задания";
  return "разрешение источника на момент загрузки не установлено";
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
  scroll.setAttribute("aria-label", "Таблица оборудования; прокрутите список для остальных позиций и вправо на узком экране");
  const inspection = element("details", null, "equipment-report__inspection");
  inspection.append(element("summary", "Подробности проверки"));
  const inspectionBody = element("div"); inspection.append(inspectionBody);
  root.append(title, summary, message, controls, filters, scroll, inspection);
  parent.append(root);
  let check = initial, search = { status: initial.search_status, revision: initial.revision };
  let filter = "all", proposal = null, disposed = false, refreshPromise = null, timer = null;
  let stream = null, observer = null, wasConnected = false;
  let rowObserver = null, resizeFrame = null;
  let scrollHandler = null;
  const measuredHeights = new Map();
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

  function detailsFor(row) {
    inspectionBody.replaceChildren(element("h4", equipmentDisplayName(row)));
    inspectionBody.append(element("p", equipmentReason(row)));
    if (row.article) inspectionBody.append(element("p", `Артикул: ${row.article}`));
    if (row.project_position || row.quantity) inspectionBody.append(element("p", `Позиция: ${row.project_position || "не указана"}. Количество: ${row.quantity || "не указано"} ${row.units || ""}.`));
    inspectionBody.append(element("p", `Запросы: ${row.metrics?.queries || 0}. Попытки загрузки: ${row.metrics?.downloads || 0}. Визуальных чтений: ${row.metrics?.vision_calls || 0}. Документов, прошедших проверку применимости: ${row.metrics?.verified_documents || 0}.`));
    for (const attempt of row.attempts || []) inspectionBody.append(element("p", `${attempt.hostname || ""}: ${EQUIPMENT_REASON_LABELS[attempt.code] || (attempt.code === "verified" ? "Документ проверен" : "Обработка не завершена")}`));
    for (const comparison of row.comparisons || []) inspectionBody.append(element("p", `Документ, стр. ${comparison.source_page}: ${comparison.manufacturer_evidence || ""}`));
    if (row.document) {
      for (const [label, value] of [["Исходный URL", row.document.source_url], ["Прочитанный URL", row.document.final_url]]) {
        const url = safeExternalSourceUrl(value);
        if (!url) continue;
        const link = element("a", `${label}: ${url}`, "equipment-report__link");
        link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; inspectionBody.append(link);
      }
      inspectionBody.append(element("p", `Snapshot: ${row.document.source_id || "—"}; SHA-256: ${row.document.sha256 || "—"}; редакция: ${row.document.revision || "не указана"}.`));
    }
    inspection.open = true;
  }

  function limitVisibleRows(table, rows, head, body) {
    rowObserver?.disconnect();
    if (resizeFrame !== null) {
      if (globalThis.cancelAnimationFrame) cancelAnimationFrame(resizeFrame); else clearTimeout(resizeFrame);
      resizeFrame = null;
    }
    if (scrollHandler) scroll.removeEventListener("scroll", scrollHandler);
    let start = -1;
    let pinnedToEnd = false;
    const height = (row) => measuredHeights.get(row.dataset.equipmentId) || 100;
    const spacer = (pixels) => {
      const row = element("tr", null, "equipment-report__spacer");
      row.setAttribute("aria-hidden", "true");
      const cell = element("td"); cell.colSpan = 5; cell.style.height = `${Math.max(0, pixels)}px`;
      row.append(cell); return row;
    };
    const draw = () => {
      if (rows.length <= 10) return;
      let offset = 0, next = 0;
      for (; next < rows.length - 10; next += 1) {
        if (offset + height(rows[next]) > (scroll.scrollTop || 0)) break;
        offset += height(rows[next]);
      }
      pinnedToEnd = scroll.scrollTop > 0 && scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 2;
      if (pinnedToEnd) next = rows.length - 10;
      offset = rows.slice(0, next).reduce((sum, row) => sum + height(row), 0);
      if (next === start) { updateSpacers(); return; }
      start = next;
      const remaining = rows.slice(start + 10).reduce((sum, row) => sum + height(row), 0);
      body.replaceChildren(spacer(offset), ...rows.slice(start, start + 10), spacer(remaining));
      scroll.dataset.visibleFrom = String(start + 1);
      scroll.dataset.visibleTo = String(Math.min(start + 10, rows.length));
    };
    const updateSpacers = () => {
      const spacers = body.querySelectorAll(".equipment-report__spacer td");
      if (spacers.length !== 2) return;
      spacers[0].style.height = `${rows.slice(0, start).reduce((sum, row) => sum + height(row), 0)}px`;
      spacers[1].style.height = `${rows.slice(start + 10).reduce((sum, row) => sum + height(row), 0)}px`;
    };
    const measure = () => {
      if (disposed || !rows.length) return;
      if (rows.length <= 10) { scroll.style.maxHeight = ""; return; }
      const visible = rows.slice(start, start + 10);
      let total = head.getBoundingClientRect().height;
      for (const row of visible) {
        const measured = row.getBoundingClientRect().height;
        if (measured > 0) measuredHeights.set(row.dataset.equipmentId, measured);
        total += height(row);
      }
      if (Number.isFinite(total) && total > 0) scroll.style.maxHeight = `${Math.ceil(total + 2)}px`;
      updateSpacers();
      if (pinnedToEnd) scroll.scrollTop = scroll.scrollHeight - scroll.clientHeight;
    };
    const schedule = () => {
      if (resizeFrame !== null || disposed) return;
      const raf = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
      resizeFrame = raf(() => { resizeFrame = null; if (!disposed) { draw(); measure(); } });
    };
    if (globalThis.ResizeObserver) { rowObserver = new ResizeObserver(schedule); rowObserver.observe(table); }
    scrollHandler = schedule;
    scroll.addEventListener("scroll", scrollHandler, { passive: true });
    draw();
    schedule();
  }

  function paint() {
    const models = equipmentRows(check);
    const uniqueModels = equipmentModels(check);
    summary.replaceChildren(); filters.replaceChildren(); controls.replaceChildren();
    const stats = check.statistics || {};
    for (const [label, value] of [
      ["Распознано записей", stats.recognized_records ?? uniqueModels.reduce((n, row) => n + (row.occurrences?.length || 1), 0)],
      ["Уникальных моделей", stats.unique_models ?? uniqueModels.length],
      ["Отправлено на поиск", stats.searched_models ?? uniqueModels.filter((row) => row.metrics?.queries > 0).length],
      ["Сохранено документов", stats.saved_documents ?? new Set(uniqueModels.filter((row) => row.document?.source_id).map((row) => row.document.source_id)).size],
      ...(check.specification?.structured_items?.length ? [["Позиций в таблице", models.length]] : []),
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
    table.append(element("caption", "Оборудование, страницы проекта и действительно использованная документация. До 10 позиций в видимой части списка."));
    const head = element("thead"), heading = element("tr");
    for (const label of ["№ п/п", "Наименование материала/оборудования", "Листы", "Сайт производителя", "Документ"]) {
      const cell = element("th", label); cell.setAttribute("scope", "col"); heading.append(cell);
    }
    head.append(heading); table.append(head);
    const body = element("tbody");
    const renderedRows = [];
    for (const [index, row] of filterEquipmentModels(models, filter).entries()) {
      const tr = element("tr");
      tr.dataset.equipmentId = equipmentRowId(row);
      tr.setAttribute("aria-rowindex", String(index + 2));
      tr.append(element("td", index + 1));
      const name = element("td");
      name.append(element("span", equipmentDisplayName(row), "equipment-report__name"));
      const more = element("button", "Подробности проверки", "equipment-report__details-button");
      more.type = "button"; more.addEventListener("click", () => detailsFor(row)); name.append(more); tr.append(name);
      const pages = element("td");
      const pageNumbers = [...new Set([...(row.pages || []), ...(row.occurrences || []).map((item) => item.page)])]
        .map(Number).filter((page) => Number.isInteger(page) && page > 0).sort((a, b) => a - b);
      for (const page of pageNumbers) {
        const link = element("a", page, "equipment-report__page-link"); link.href = `#analysis-page-${page}`;
        link.setAttribute("aria-label", `${equipmentDisplayName(row)}: показать на листе ${page}`);
        link.addEventListener("click", async (event) => {
          event?.preventDefault?.();
          const regions = (row.occurrences || []).filter((item) => Number(item.page) === page).flatMap((item) => item.visual_regions || []);
          await focusPageEvidence(page, { regions });
        });
        pages.append(link);
      }
      if (!pageNumbers.length) pages.textContent = "Не указаны";
      tr.append(pages);
      const site = element("td");
      const siteUrl = safeExternalSourceUrl(row.manufacturer_site?.url);
      if (siteUrl && ["project", "analog"].includes(row.manufacturer_site.relationship)) {
        const link = element("a", row.manufacturer_site.name || "Сайт производителя", "equipment-report__link");
        link.href = siteUrl; link.target = "_blank"; link.rel = "noopener noreferrer"; site.append(link);
        if (row.manufacturer_site.relationship === "analog") site.append(element("small", "Аналог — производитель проектного изделия не установлен"));
      } else site.textContent = "Не найден";
      tr.append(site);
      const documentCell = element("td");
      const sourceUrl = row.document?.source_id ? safeExternalSourceUrl(row.document.final_url || row.document.source_url) : null;
      if (sourceUrl) {
        const original = element("a", "Прочитанный документ", "equipment-report__link");
        original.href = sourceUrl; original.target = "_blank"; original.rel = "noopener noreferrer"; documentCell.append(original);
      }
      if (equipmentSnapshotUrl(jobId, row.document?.source_id)) {
        const download = element("button", "Сохранённый документ", "equipment-report__action");
        download.type = "button"; download.addEventListener("click", () => snapshot(row.document.source_id));
        documentCell.append(download);
      } else if (!sourceUrl) documentCell.append(element("span", "Документ не подтверждён"));
      if (row.document?.source_id) documentCell.append(element("small", `Редакция: ${row.document.revision || "не указана"}; ${equipmentSourceTrustLabel(row.document)}. Применимость к модели и исполнению проверяется отдельно.`));
      documentCell.append(element("small", equipmentDocumentStatus(row), `equipment-report__status equipment-report__status--${row.status || "incomplete"}`));
      tr.append(documentCell); body.append(tr); renderedRows.push(tr);
    }
    table.setAttribute("aria-rowcount", String(renderedRows.length + 1));
    table.append(body); scroll.replaceChildren(table); limitVisibleRows(table, renderedRows, head, body);
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

  function dispose() {
    disposed = true; abort.abort(); stream?.abort(); clearTimeout(timer); observer?.disconnect(); rowObserver?.disconnect();
    if (scrollHandler) scroll.removeEventListener("scroll", scrollHandler);
    if (resizeFrame !== null) { if (globalThis.cancelAnimationFrame) cancelAnimationFrame(resizeFrame); else clearTimeout(resizeFrame); }
  }
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
