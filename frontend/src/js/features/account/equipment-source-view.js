// frontend/src/js/features/account/equipment-source-view.js

/** Компактные представления каталога: разрешение сайта отдельно от применимости документа. */
import { safeExternalSourceUrl } from "../analysis/finding-provenance.js";

export function node(tag, text = "", className = "") {
  /** Создаёт узел с безопасным текстом без интерпретации HTML источника. */
  const result = document.createElement(tag);
  result.textContent = text; result.className = className;
  return result;
}

export function button(text, action, className = "") {
  /** Создаёт кнопку действия, не отправляющую внешнюю форму. */
  const result = node("button", text, className);
  result.type = "button"; result.onclick = action;
  return result;
}

/** Только подтверждённый и включённый сайт считается глобально разрешённым. */
export const isUsed = (source) => source.status === "trusted" && source.enabled === true;

export function usage(status, enabled) {
  /** Показывает смысл состояния без внутренних enum. */
  if (status === "pending") return "Ожидает решения";
  return isUsed({ status, enabled }) ? "Используется для поиска" : "Не используется";
}

function date(value) {
  /** Не выдаёт повреждённую дату за время изменения. */
  const parsed = new Date(value);
  return value && Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "Дата не указана";
}

function actor(value, session, history = false) {
  /** Известное имя показывается сразу, чужой UUID остаётся в подробной истории. */
  if (value && value === session.user?.id) return session.user.display_name || session.user.login || "Вы";
  if (!value) return "Имя не указано";
  if (/^[0-9a-f-]{36}$/i.test(value) && !history) return "Имя пользователя недоступно";
  return String(value);
}

function external(text, value) {
  /** Разрешает только безопасные внешние ссылки. */
  const url = safeExternalSourceUrl(value);
  if (!url) return node("span", text);
  const link = node("a", text);
  link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
  return link;
}

export function sourceGroup(name, sources, { session, busy, change, history, collapsed }) {
  /** Группирует точные домены и предлагает одно немедленное изменение. */
  const group = node("details", "", "equipment-sources__group");
  group.open = !collapsed.has(name);
  group.append(node("summary", `${name} · ${sources.length} сайта`));
  group.ontoggle = () => { collapsed[group.open ? "delete" : "add"](name); };
  for (const source of sources) {
    const row = node("div", "", "equipment-sources__row");
    const domain = external(source.hostname, "https://" + source.hostname);
    domain.className = "equipment-sources__domain";
    const state = node("span", usage(source.status, source.enabled), "equipment-sources__state");
    state.dataset.used = String(isUsed(source));
    const changed = node("small", date(source.updated_at) + " · " + actor(source.updated_by, session));
    const toggle = node("input"); toggle.type = "checkbox";
    toggle.setAttribute("role", "switch"); toggle.setAttribute("aria-label", "Использовать " + source.hostname);
    toggle.dataset.sourceToggle = source.id; toggle.checked = isUsed(source); toggle.disabled = busy.has(source.id);
    toggle.onchange = () => change(source, toggle.checked, toggle);
    const actions = node("details", "", "equipment-sources__actions");
    actions.append(node("summary", "Действия"), button("История изменений", () => history(source)));
    row.append(domain, state, changed, toggle, actions); group.append(row);
  }
  return group;
}

export function pendingCard(source, { busy, change }) {
  /** Новый сайт не получает глобального разрешения из поисковой выдачи. */
  const card = node("article", "", "equipment-sources__pending");
  card.append(node("strong", source.hostname), node("p", source.manufacturer));
  if (source.example_model) card.append(node("small", "Пример модели: " + source.example_model));
  if (source.example_url) {
    const url = safeExternalSourceUrl(source.example_url);
    if (url && new URL(url).hostname === source.hostname) card.append(external("Исходная ссылка ↗", url));
  }
  const actions = node("div", "", "equipment-sources__buttons");
  for (const [label, allowed] of [["Использовать", true], ["Не использовать", false]]) {
    const control = button(label, () => change(source, allowed, control), allowed ? "equipment-sources__primary" : "");
    control.disabled = busy.has(source.id); actions.append(control);
  }
  card.append(actions); return card;
}

export function auditTable(events, session) {
  /** Исходный аудит не меняется; отображаются прежнее и новое решения. */
  const table = node("table", "", "equipment-sources__audit"), header = node("tr"), head = node("thead");
  for (const label of ["Дата и время", "Кто изменил", "Изменение", "Причина"]) header.append(node("th", label));
  head.append(header); table.append(head);
  const body = node("tbody");
  for (const event of events) {
    const row = node("tr");
    const before = event.previous_status ? usage(event.previous_status, event.previous_enabled) : "Новый сайт";
    const after = usage(event.new_status, event.new_enabled);
    const changed = event.previous_status !== event.new_status || event.previous_enabled !== event.new_enabled;
    const action = changed ? (event.new_status === "trusted" && event.new_enabled
      ? "Разрешил использование" : event.new_status === "blocked" || event.new_enabled === false
        ? "Запретил использование" : "Оставил на рассмотрении") : "Изменил настройки сайта";
    for (const value of [date(event.occurred_at), actor(event.actor, session, true),
      `${action}. ${before} → ${after}`, event.reason || "—"]) row.append(node("td", value));
    body.append(row);
  }
  table.append(body); return table;
}

export function openDialog(parent, title) {
  /** Нативное окно поддерживает Escape, фокус и возврат к вызвавшему элементу. */
  const dialog = node("dialog", "", "equipment-sources__dialog"), heading = node("h3", title);
  heading.id = "equipment-source-dialog-title"; dialog.setAttribute("aria-labelledby", heading.id);
  const close = button("Закрыть", () => dialog.close()); close.className = "equipment-sources__close";
  dialog.append(heading, close); dialog.addEventListener("close", () => dialog.remove());
  parent.append(dialog); return dialog;
}
