// frontend/src/js/features/admin/review-access.js

/** Отдельное назначение ревью с подтверждением сервера и защитой от старой версии. */
import { changeReviewAccess } from "./api.js";

/** Показывает действующие права и сохраняет ручное назначение по версии сервера. */
export function createReviewAccessControl(user, { onChanged, changeAccess = changeReviewAccess } = {}) {
  const root = document.createElement("div");
  root.className = "admin-review-access";
  const label = document.createElement("label");
  label.className = "admin-review-access__label";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = user.review_access === true;
  checkbox.disabled = user.review_access_editable !== true;
  checkbox.dataset.adminReviewAccess = user.user_id;
  const title = document.createElement("span");
  title.textContent = "Доступ к ревью";
  label.append(checkbox, title);
  const message = document.createElement("span");
  message.className = "admin-review-access__message";
  message.setAttribute("role", "status");
  if (user.review_access_automatic) message.textContent = "По роли";
  else if (checkbox.disabled) message.textContent = "Доступен активному проектировщику";
  root.append(label, message);
  let saved = checkbox.checked;
  let version = user.authorization_version;
  let busy = false;
  let stale = false;
  checkbox.addEventListener("change", async () => {
    if (busy || stale || user.review_access_editable !== true) {
      checkbox.checked = saved;
      return;
    }
    busy = true;
    checkbox.disabled = true;
    message.textContent = "Сохраняем…";
    try {
      const result = await changeAccess(user.user_id, checkbox.checked, version);
      saved = result.review_access;
      version = result.user.authorization_version;
      checkbox.checked = saved;
      Object.assign(user, result.user, {
        review_access: saved,
        review_access_automatic: result.review_access_automatic,
        review_access_editable: result.review_access_editable,
      });
      message.textContent = "Сохранено. Пользователю нужно войти снова.";
    } catch (error) {
      checkbox.checked = saved;
      stale = error.status === 409;
      message.textContent = stale
        ? "Права уже изменены. Обновите список пользователей."
        : error.detail ?? "Не удалось изменить доступ к ревью.";
      return;
    } finally {
      busy = false;
      checkbox.disabled = stale || user.review_access_editable !== true;
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
