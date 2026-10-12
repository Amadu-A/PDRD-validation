// frontend/src/js/features/admin/equipment-sources-access.js

/** Отдельное назначение управления источниками оборудования с подтверждением сервера и защитой от старой версии. */
import { changeEquipmentSourcesAccess } from "./api.js";

/** Показывает действующие права и сохраняет ручное назначение по версии сервера. */
export function createEquipmentSourcesAccessControl(user, { onChanged, changeAccess = changeEquipmentSourcesAccess } = {}) {
  const root = document.createElement("div");
  root.className = "admin-review-access";
  const label = document.createElement("label");
  label.className = "admin-review-access__label";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = user.equipment_sources_access === true;
  checkbox.disabled = user.equipment_sources_access_editable !== true;
  checkbox.dataset.adminEquipmentSourcesAccess = user.user_id;
  const title = document.createElement("span");
  title.textContent = "Источники документации оборудования";
  label.append(checkbox, title);
  const message = document.createElement("span");
  message.className = "admin-review-access__message";
  message.setAttribute("role", "status");
  if (user.equipment_sources_access_automatic) message.textContent = "По роли";
  else if (checkbox.disabled) message.textContent = "Доступен активному пользователю";
  root.append(label, message);
  let saved = checkbox.checked;
  let version = user.authorization_version;
  let busy = false;
  let stale = false;
  checkbox.addEventListener("change", async () => {
    if (busy || stale || user.equipment_sources_access_editable !== true) {
      checkbox.checked = saved;
      return;
    }
    busy = true;
    checkbox.disabled = true;
    message.textContent = "Сохраняем…";
    try {
      const result = await changeAccess(user.user_id, checkbox.checked, version);
      saved = result.equipment_sources_access;
      version = result.user.authorization_version;
      checkbox.checked = saved;
      Object.assign(user, result.user, {
        equipment_sources_access: saved,
        equipment_sources_access_automatic: result.equipment_sources_access_automatic,
        equipment_sources_access_editable: result.equipment_sources_access_editable,
      });
      message.textContent = "Сохранено. Пользователю нужно войти снова.";
    } catch (error) {
      checkbox.checked = saved;
      stale = error.status === 409;
      message.textContent = stale
        ? "Права уже изменены. Обновите список пользователей."
        : error.detail ?? "Не удалось изменить доступ к источникам оборудования.";
      return;
    } finally {
      busy = false;
      checkbox.disabled = stale || user.equipment_sources_access_editable !== true;
    }
    try {
      await onChanged?.();
    } catch {
      stale = true;
      checkbox.disabled = true;
      message.textContent = "Доступ сохранён. Обновите страницу для загрузки списка.";
    }
  });
  return root;
}
