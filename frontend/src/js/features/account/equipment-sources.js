// frontend/src/js/features/account/equipment-sources.js

/** Управляет точными EQ-доменами только при подтверждённом серверном разрешении. */
import { authenticatedRequest } from "../auth/api.js";

const versions = new WeakMap();

export function canManageEquipmentSources(session) {
  /** Проверяет право, полученное из действующей серверной сессии. */
  return Boolean(session.authenticated
    && session.user?.permissions?.includes("equipment_sources.manage"));
}

export function listEquipmentSources(status = "", query = "") {
  /** Читает каталог через API Gateway с серверной повторной проверкой права. */
  const params = new URLSearchParams();
  if (status) params.set("source_status", status);
  if (query) params.set("query", query);
  const suffix = params.size ? "?" + params.toString() : "";
  return authenticatedRequest("/api/v1/equipment-sources" + suffix);
}

export function listEquipmentManufacturers() {
  /** Читает имена производителей для формы добавления. */
  return authenticatedRequest("/api/v1/equipment-sources/manufacturers");
}

export function saveEquipmentSource(payload) {
  /** Сохраняет решение с CSRF токеном текущей сессии. */
  return authenticatedRequest("/api/v1/equipment-sources", "PUT", payload);
}

export function readEquipmentSourceAudit(sourceId) {
  /** Читает историю доверия конкретного домена. */
  return authenticatedRequest(
    "/api/v1/equipment-sources/" + encodeURIComponent(sourceId) + "/audit",
  );
}

function element(tag, content = "") {
  const node = document.createElement(tag);
  node.textContent = content;
  return node;
}

function sourceRow(source, reload, status) {
  const row = element("li");
  const heading = element("strong", source.manufacturer + " · " + source.hostname);
  const meta = element("p",
    "Статус: " + source.status
    + " · Использований: " + Number(source.uses_count || 0)
    + " · Найден: " + (source.registration_source || "неизвестно")
    + " · Модель: " + (source.example_model || "не указана")
    + " · Пример URL: " + (source.example_url || "нет"));
  const controls = element("div");
  controls.className = "account-actions";
  const selection = element("select");
  for (const value of ["pending", "trusted", "blocked"]) {
    const option = element("option", value);
    option.value = value;
    selection.append(option);
  }
  selection.value = source.status;
  const enabled = element("input");
  enabled.type = "checkbox";
  enabled.checked = Boolean(source.enabled);
  const enabledLabel = element("label", " Включён");
  enabledLabel.prepend(enabled);
  const http = element("input");
  http.type = "checkbox";
  http.checked = Boolean(source.allow_http);
  const httpLabel = element("label", " Разрешить HTTP");
  httpLabel.prepend(http);
  const reason = element("input");
  reason.type = "text";
  reason.required = true;
  reason.maxLength = 1000;
  reason.placeholder = "Причина изменения";
  reason.setAttribute("aria-label", "Причина изменения источника " + source.hostname);
  const save = element("button", "Сохранить");
  save.type = "button";
  save.onclick = async () => {
    if (!reason.value.trim()) {
      status.textContent = "Укажите причину изменения.";
      return;
    }
    save.disabled = true;
    try {
      await saveEquipmentSource({
        manufacturer: source.manufacturer,
        hostname: source.hostname,
        status: selection.value,
        enabled: enabled.checked,
        allow_http: http.checked,
        reason: reason.value.trim(),
      });
      await reload();
    } catch (error) {
      status.textContent = error.detail ?? "Не удалось сохранить источник.";
      save.disabled = false;
    }
  };
  const history = element("button", "История");
  history.type = "button";
  const audit = element("ul");
  history.onclick = async () => {
    history.disabled = true;
    try {
      const events = await readEquipmentSourceAudit(source.id);
      audit.replaceChildren();
      for (const event of events) {
        audit.append(element("li",
          String(event.occurred_at || "") + " · "
          + (event.previous_status || "новый") + " → " + event.new_status
          + " · " + event.reason + " · " + event.actor));
      }
      if (!events.length) audit.append(element("li", "Истории пока нет."));
    } catch (error) {
      status.textContent = error.detail ?? "Не удалось прочитать историю.";
    } finally {
      history.disabled = false;
    }
  };
  controls.append(selection, enabledLabel, httpLabel, reason, save, history);
  row.append(heading, meta, controls, audit);
  return row;
}

export async function renderEquipmentSources(root, session) {
  /** Обновляет каталог без показа старого ответа после смены пользователя. */
  const section = root.querySelector("[data-equipment-sources]");
  if (!section) return;
  const version = (versions.get(section) ?? 0) + 1;
  versions.set(section, version);
  const current = () => versions.get(section) === version;
  const allowed = canManageEquipmentSources(session);
  section.hidden = !allowed;
  if (!allowed) return;
  const status = root.querySelector("[data-equipment-source-status]");
  const list = root.querySelector("[data-equipment-source-list]");
  const filterStatus = root.querySelector("[data-equipment-filter-status]");
  const filterQuery = root.querySelector("[data-equipment-filter-query]");
  const filterApply = root.querySelector("[data-equipment-filter-apply]");
  const manufacturers = root.querySelector("[data-equipment-manufacturers]");
  const form = root.querySelector("[data-equipment-source-form]");

  const load = async () => {
    status.textContent = "Загружаем источники оборудования…";
    try {
      const [sources, makers] = await Promise.all([
        listEquipmentSources(filterStatus.value, filterQuery.value.trim()),
        listEquipmentManufacturers(),
      ]);
      if (!current()) return;
      list.replaceChildren();
      for (const source of sources) {
        list.append(sourceRow(source, load, status));
      }
      manufacturers.replaceChildren();
      for (const maker of makers) {
        const option = element("option");
        option.value = maker.name;
        manufacturers.append(option);
      }
      status.textContent = sources.length ? "" : "Источников по фильтру нет.";
    } catch (error) {
      if (current()) status.textContent = error.detail ?? "Не удалось загрузить каталог.";
    }
  };
  filterApply.onclick = () => { void load(); };
  form.onsubmit = async (event) => {
    event.preventDefault();
    const values = new FormData(form);
    try {
      await saveEquipmentSource({
        manufacturer: String(values.get("manufacturer") || "").trim(),
        hostname: String(values.get("hostname") || "").trim(),
        status: String(values.get("status") || "pending"),
        enabled: true,
        allow_http: values.get("allow_http") === "on",
        reason: String(values.get("reason") || "").trim(),
      });
      if (!current()) return;
      form.reset();
      await load();
    } catch (error) {
      if (current()) status.textContent = error.detail ?? "Не удалось добавить источник.";
    }
  };
  await load();
}
