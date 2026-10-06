// frontend/src/js/features/admin/role-form.js

/** Назначает роль и несколько разделов одним запросом с CAS и аудитом. */
import { changeUserRole, getUserSections, listSectionCatalog } from "./api.js";

const roles = [
  ["", "Без рабочей роли"], ["designer", "Проектировщик"],
  ["department_head", "Руководитель отдела"], ["platform_admin", "Администратор платформы"],
];
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

/** Проверяет область роли; руководитель ограничен отмеченными разделами. */
export function roleScope(role, sectionIds = []) {
  if (role === "designer") return { kind: "own" };
  if (role === "platform_admin") return { kind: "platform" };
  if (role === "department_head") {
    if (!Array.isArray(sectionIds) || !sectionIds.length || sectionIds.some((id) => !UUID.test(id))) {
      throw new Error("Для руководителя отметьте хотя бы один раздел.");
    }
    return { kind: "sections" };
  }
  return null;
}

/** Показывает живые названия разделов и сохраняет выбранный набор атомарно с ролью. */
export function createRoleForm(user, onChanged) {
  const form = document.createElement("form");
  form.className = "admin-role-form";
  const roleLabel = document.createElement("label");
  roleLabel.textContent = "Роль PDRD";
  const role = document.createElement("select");
  role.name = "role";
  for (const [value, label] of roles) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    role.append(option);
  }
  role.value = user.currentRole ?? user.roles?.[0] ?? "";
  role.disabled = Boolean(user.roleLocked);
  roleLabel.append(role);
  const fields = document.createElement("fieldset");
  fields.className = "admin-role-form__sections";
  const legend = document.createElement("legend");
  legend.textContent = "Доступные разделы документации";
  const choices = document.createElement("div");
  choices.className = "admin-role-form__choices";
  const hint = document.createElement("p");
  hint.textContent = "Разделы берутся с главной страницы. Администратор имеет доступ ко всем разделам; личные пакеты остаются у владельца.";
  fields.append(legend, choices, hint);
  const reload = document.createElement("button");
  reload.type = "button";
  reload.textContent = "Обновить разделы";
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Сохранить роль и доступ";
  const message = document.createElement("p");
  message.className = "admin-role-form__message";
  message.setAttribute("role", "status");
  let loaded = false;
  let stale = false;
  let checkboxes = [];

  /** Обновляет возможность записи с учётом роли, статуса и полученной версии. */
  function updateControls() {
    save.disabled = !loaded || stale || Boolean(user.roleLocked)
      || (user.status && user.status !== "active");
    fields.disabled = Boolean(user.roleLocked) || role.value === "platform_admin";
  }

  /** Загружает каталог и назначения; отказ не превращается в пустой набор для записи. */
  async function load() {
    loaded = false;
    reload.disabled = true;
    updateControls();
    message.textContent = "Загружаем разделы…";
    try {
      const [catalog, access] = await Promise.all([listSectionCatalog(), getUserSections(user.user_id)]);
      if (!Array.isArray(catalog) || !Array.isArray(access.section_ids)
        || access.authorization_version !== user.authorization_version) {
        throw new Error("Версия прав изменилась. Откройте карточку пользователя заново.");
      }
      const selected = new Set(access.section_ids);
      checkboxes = [];
      choices.replaceChildren(...catalog.map((section) => {
        const label = document.createElement("label");
        label.className = "admin-role-form__section";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.name = "section_ids";
        input.value = section.section_id;
        input.checked = selected.has(section.section_id);
        checkboxes.push(input);
        const text = document.createElement("span");
        text.textContent = section.name;
        label.append(input, text);
        return label;
      }));
      loaded = true;
      message.textContent = catalog.length ? "" : "Разделов пока нет. Создайте раздел на главной странице.";
    } catch (error) {
      message.textContent = error.detail ?? error.message ?? "Не удалось загрузить разделы.";
    } finally {
      reload.disabled = false;
      updateControls();
    }
  }

  role.addEventListener("change", updateControls);
  reload.addEventListener("click", () => { void load(); });
  form.append(roleLabel, fields, reload, save, message);
  updateControls();
  if (!user.roleLocked && (!user.status || user.status === "active")) void load();
  else message.textContent = user.roleLocked ? "Назначение из AD меняется в источнике." : "Изменения доступны только активным пользователям.";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (save.disabled) return;
    save.disabled = true;
    message.textContent = "Сохраняем…";
    try {
      const ids = checkboxes.filter((entry) => entry.checked).map((entry) => entry.value);
      const scope = roleScope(role.value, ids);
      await changeUserRole(user.user_id, role.value || null, scope, user.authorization_version, ids);
      stale = true;
      message.textContent = "Роль и доступ обновлены.";
      await onChanged();
    } catch (error) {
      stale = error.status === 409;
      message.textContent = error.detail ?? error.message ?? "Не удалось сохранить доступ.";
    } finally { updateControls(); }
  });
  return form;
}
