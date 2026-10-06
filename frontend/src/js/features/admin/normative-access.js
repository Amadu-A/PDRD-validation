// frontend/src/js/features/admin/normative-access.js

/** Отдельное назначение удаления нормативных объектов с подтверждением сервера и защитой от старой версии. */
import { changeNormativeAccess } from "./api.js";

/** Показывает действующие права и сохраняет ручное назначение по версии сервера. */
export function createNormativeAccessControl(user, { onChanged, changeAccess = changeNormativeAccess } = {}) {
  const root = document.createElement("div");
  root.className = "admin-review-access";
  const label = document.createElement("label");
  label.className = "admin-review-access__label";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = user.normative_access === true;
  checkbox.disabled = user.normative_access_editable !== true;
  checkbox.dataset.adminNormativeAccess = user.user_id;
  const title = document.createElement("span");
  title.textContent = "Доступ к изменению нормативного блока";
  label.append(checkbox, title);
  const message = document.createElement("span");
  message.className = "admin-review-access__message";
  message.setAttribute("role", "status");
  if (user.normative_access_automatic) message.textContent = "По роли";
  else if (checkbox.disabled) message.textContent = "Доступен активному проектировщику или руководителю";
  root.append(label, message);
  let saved = checkbox.checked;
  let version = user.authorization_version;
  let busy = false;
  let stale = false;
  checkbox.addEventListener("change", async () => {
    if (busy || stale || user.normative_access_editable !== true) {
      checkbox.checked = saved;
      return;
    }
    busy = true;
    checkbox.disabled = true;
    message.textContent = "Сохраняем…";
    try {
      const result = await changeAccess(user.user_id, checkbox.checked, version);
      saved = result.normative_access;
      version = result.user.authorization_version;
      checkbox.checked = saved;
      Object.assign(user, result.user, {
        normative_access: saved,
        normative_access_automatic: result.normative_access_automatic,
        normative_access_editable: result.normative_access_editable,
      });
      message.textContent = "Сохранено. Пользователю нужно войти снова.";
    } catch (error) {
      checkbox.checked = saved;
      stale = error.status === 409;
      message.textContent = stale
        ? "Права уже изменены. Обновите список пользователей."
        : error.detail ?? "Не удалось изменить доступ к изменению нормативного блока.";
      return;
    } finally {
      busy = false;
      checkbox.disabled = stale || user.normative_access_editable !== true;
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
