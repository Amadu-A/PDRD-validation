// frontend/src/js/features/account/equipment-sources.js

/** Управляет разрешением точных EQ-доменов в подтверждённой серверной сессии. */
import { authenticatedRequest } from "../auth/api.js";
import { node, button, isUsed, sourceGroup, pendingCard, auditTable, openDialog } from "./equipment-source-view.js";

const versions = new WeakMap(), dialogs = new WeakMap();

export function canManageEquipmentSources(session) {
  /** Проверяет назначенное сервером право независимо от роли и unsafe режима. */
  return Boolean(session.authenticated && session.user?.permissions?.includes("equipment_sources.manage"));
}

export function listEquipmentSources(status = "", query = "") {
  /** Читает существующий каталог с повторной серверной проверкой доступа. */
  const params = new URLSearchParams();
  if (status) params.set("source_status", status);
  if (query) params.set("query", query);
  return authenticatedRequest("/api/v1/equipment-sources" + (params.size ? "?" + params : ""));
}

export function listEquipmentManufacturers() {
  /** Читает подтверждённые имена и алиасы для добавления сайта. */
  return authenticatedRequest("/api/v1/equipment-sources/manufacturers");
}

export function saveEquipmentSource(payload) {
  /** Сохраняет решение с CSRF; отсутствие allow_http сохраняет прежний транспорт. */
  return authenticatedRequest("/api/v1/equipment-sources", "PUT", {
    ...payload, reason: payload.reason || "Сохранение источника через личный кабинет",
  });
}

export function readEquipmentSourceAudit(id) {
  /** Читает неизменённую историю решения по домену. */
  return authenticatedRequest("/api/v1/equipment-sources/" + encodeURIComponent(id) + "/audit");
}

export function siteHostname(value) {
  /** Принимает HTTPS-адрес, сохраняет точный hostname, включая www. */
  const text = value.trim();
  let url;
  try { url = new URL(text.includes("://") ? text : "https://" + text); }
  catch { throw new Error("Укажите адрес публичного сайта, например www.example.com."); }
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (url.protocol !== "https:" || url.username || url.password || url.port
      || !host.includes(".") || host.length > 253 || /^[\d.]+$/.test(host)
      || !host.split(".").every((part) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(part))
      || /\.(local|localhost|internal|test|invalid)$/i.test(host)) {
    throw new Error("Нужен публичный HTTPS-сайт без порта, IP-адреса и данных входа.");
  }
  return host;
}

export async function renderEquipmentSources(root, session) {
  /** Старые ответы и обработчики теряют действие при смене или отзыве сессии. */
  const allowed = canManageEquipmentSources(session);
  const link = root.querySelector("[data-account-equipment-sources-link]");
  if (link) link.hidden = !allowed;
  const section = root.querySelector("[data-equipment-sources]");
  if (!section) return;
  const version = (versions.get(section) || 0) + 1;
  versions.set(section, version); dialogs.get(section)?.close();
  const current = () => versions.get(section) === version;
  section.hidden = !allowed;
  const app = root.querySelector("[data-equipment-source-app]");
  const status = root.querySelector("[data-equipment-source-status]");
  app.replaceChildren();
  if (!allowed) return;
  let sources = [], makers = [], filter = "all", query = "";
  const busy = new Set(), collapsed = new Set();
  const toolbar = node("div", "", "equipment-sources__toolbar");
  const search = node("input");
  search.type = "search"; search.placeholder = "Найти производителя или сайт…";
  search.setAttribute("aria-label", "Поиск по производителю или сайту");
  const tabs = node("div", "", "equipment-sources__tabs");
  const add = button("＋ Добавить сайт", () => addSite(), "equipment-sources__primary");
  toolbar.append(search, tabs, add);
  const layout = node("div", "", "equipment-sources__layout");
  const list = node("div", "", "equipment-sources__list"), pending = node("aside", "", "equipment-sources__new");
  layout.append(list, pending); app.append(toolbar, layout);
  search.oninput = () => { query = search.value.trim().toLocaleLowerCase(); paint(); };

  function paint() {
    /** Фильтрует подтверждённые сервером данные, не меняя доверие. */
    if (!current()) return;
    list.replaceChildren(); pending.replaceChildren(); tabs.replaceChildren();
    const known = sources.filter((item) => item.status !== "pending");
    for (const [value, label, count] of [["all", "Все", known.length],
      ["used", "Используем", known.filter(isUsed).length],
      ["unused", "Не используем", known.filter((item) => !isUsed(item)).length]]) {
      const tab = button(`${label} (${count})`, () => { filter = value; paint(); });
      tab.setAttribute("aria-pressed", String(filter === value)); tabs.append(tab);
    }
    const matches = (item) => (item.manufacturer + " " + item.hostname).toLocaleLowerCase().includes(query);
    const visible = known.filter(matches).filter((item) => filter === "all" || isUsed(item) === (filter === "used"));
    const groups = new Map();
    for (const item of visible) {
      if (!groups.has(item.manufacturer)) groups.set(item.manufacturer, []);
      groups.get(item.manufacturer).push(item);
    }
    for (const [name, items] of groups) list.append(sourceGroup(name, items, { session, busy, change, history, collapsed }));
    if (!visible.length) list.append(node("p", "Сайтов по этому фильтру нет."));
    const candidates = sources.filter((item) => item.status === "pending" && matches(item));
    pending.append(node("h3", "Новые сайты · " + candidates.length),
      node("p", "Ожидают решения. До общего разрешения могут использоваться только в заданиях, где разрешены неподтверждённые источники."));
    for (const item of candidates) pending.append(pendingCard(item, { busy, change }));
    if (!candidates.length) pending.append(node("p", "Новых сайтов нет."));
    pending.append(button("＋ Добавить сайт вручную", () => addSite()));
  }

  async function load() {
    /** Повторная загрузка возвращает фактическое состояние API. */
    status.textContent = "Загружаем источники оборудования…";
    try {
      const result = await Promise.all([listEquipmentSources(), listEquipmentManufacturers()]);
      if (!current()) return;
      [sources, makers] = result; status.textContent = ""; paint();
    } catch (error) {
      if (current()) status.textContent = error.detail || "Не удалось загрузить каталог. Обновите страницу.";
    }
  }

  async function change(source, enabled, control) {
    /** Блокирует повторную отправку и возвращает подтверждённое состояние при отказе. */
    if (!current() || busy.has(source.id)) return;
    const restoreFocus = document.activeElement === control;
    busy.add(source.id); control.disabled = true; paint(); status.textContent = "Сохраняем решение…";
    try {
      const saved = await saveEquipmentSource({ manufacturer: source.manufacturer,
        hostname: source.hostname, status: enabled ? "trusted" : "blocked", enabled,
        reason: enabled ? "Разрешил использование сайта" : "Запретил использование сайта" });
      if (!current()) return;
      sources = sources.map((item) => item.id === source.id ? { ...item, ...saved } : item);
      status.textContent = "Настройка сохранена.";
    } catch (error) {
      if (current()) status.textContent = (error.detail || "Не удалось сохранить решение.")
        + " Показано последнее подтверждённое состояние. Обновите страницу для сверки.";
    } finally {
      busy.delete(source.id);
      if (current()) {
        paint();
        if (restoreFocus) [...list.querySelectorAll("[data-source-toggle]")]
          .find((item) => item.dataset.sourceToggle === source.id)?.focus();
      }
    }
  }

  function dialog(title) {
    /** Одновременно открыто одно окно; отзыв доступа закрывает его. */
    dialogs.get(section)?.close();
    const result = openDialog(section, title); dialogs.set(section, result); return result;
  }

  async function history(source) {
    /** Не показывает ответ истории после выхода пользователя. */
    if (!current()) return;
    const window = dialog("История изменений — " + source.hostname);
    const content = node("div", "Загружаем историю…", "equipment-sources__history");
    window.append(content); window.showModal();
    try {
      const events = await readEquipmentSourceAudit(source.id);
      if (!current() || !window.open) return;
      content.textContent = "";
      content.replaceChildren(events.length ? auditTable(events, session) : node("p", "Истории пока нет."));
    } catch (error) { if (current() && window.open) content.textContent = error.detail || "Не удалось прочитать историю."; }
  }

  function addSite() {
    /** Два поля; добавление разрешает только проверенный сервером домен. */
    if (!current()) return;
    const window = dialog("Добавить сайт"), form = node("form"), maker = node("input"), address = node("input");
    const choices = node("datalist"); choices.id = "equipment-manufacturer-choices";
    maker.required = address.required = true; maker.maxLength = 200; address.maxLength = 2048;
    maker.name = "manufacturer"; address.name = "hostname"; maker.setAttribute("list", choices.id);
    maker.placeholder = "Например: Schneider Electric"; address.placeholder = "Например: www.se.com";
    for (const item of makers) { const option = node("option"); option.value = item.name; choices.append(option); }
    const makerLabel = node("label", "Производитель *"), addressLabel = node("label", "Адрес сайта *");
    makerLabel.append(maker); addressLabel.append(address);
    const errorText = node("p", "", "equipment-sources__error"); errorText.setAttribute("role", "alert");
    const submit = node("button", "Добавить", "equipment-sources__primary"); submit.type = "submit";
    const cancel = button("Отмена", () => window.close());
    const actions = node("div", "", "equipment-sources__buttons"); actions.append(cancel, submit);
    form.append(makerLabel, addressLabel, choices,
      node("small", "HTTPS используется по умолчанию. Сохраняется точный домен; www — отдельный сайт."), errorText, actions);
    let saving = false;
    form.onsubmit = async (event) => {
      event.preventDefault(); if (!current() || saving) return;
      try {
        const hostname = siteHostname(address.value), manufacturer = maker.value.trim();
        if (!manufacturer) throw new Error("Укажите производителя.");
        const normalize = (value) => value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
        const canonical = makers.find((item) => [item.name, ...(item.aliases || [])]
          .some((name) => normalize(name) === normalize(manufacturer)))?.name || manufacturer;
        if (sources.some((item) => normalize(item.manufacturer) === normalize(canonical) && item.hostname === hostname)) {
          throw new Error("Этот сайт уже добавлен для производителя.");
        }
        saving = true; submit.disabled = cancel.disabled = maker.disabled = address.disabled = true;
        const saved = await saveEquipmentSource({ manufacturer: canonical, hostname,
          status: "trusted", enabled: true, create_only: true, reason: "Добавил сайт и разрешил использование" });
        if (!current()) return;
        sources.push(saved); window.close(); paint(); status.textContent = "Сайт добавлен и разрешён для поиска.";
      } catch (error) { if (current()) errorText.textContent = error.detail || error.message || "Не удалось добавить сайт."; }
      finally { saving = false; submit.disabled = cancel.disabled = maker.disabled = address.disabled = false; }
    };
    window.append(form); window.showModal();
  }
  await load();
}
