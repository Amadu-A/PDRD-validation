// frontend/src/js/features/admin/users.js

/** Список и фильтр реальных пользователей без сохранения данных в браузере. */
import { createRoleForm } from "./role-form.js";
import { getUserRoles } from "./api.js";
import { createReviewAccessControl } from "./review-access.js";
import { createNormativeAccessControl } from "./normative-access.js";

const statusLabels = {
  active: "Активен",
  blocked: "Заблокирован",
  pending_verification: "Ожидает подтверждения email",
};

/** Выбирает текущую роль, включая администратора, и отмечает назначения из AD. */
export function roleEditorState(roles, assignments) {
  const worker = assignments.find((entry) =>
    entry.role === "designer" || entry.role === "department_head");
  const administrator = roles.includes("platform_admin");
  return {
    currentRole: administrator ? "platform_admin" : worker?.role ?? "",
    scope: worker?.scope ?? null,
    roleLocked: assignments.some((entry) => entry.source === "ad_group"),
  };
}

export function filterUsers(users, query) {
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  return users.filter((user) => [user.display_name, user.login, user.email]
    .some((value) => String(value ?? "").toLocaleLowerCase("ru-RU").includes(needle)));
}

export function renderUsers(root, users, onChanged) {
  const list = root.querySelector("[data-admin-users]");
  const query = root.querySelector("[data-admin-search]").value;
  const filtered = filterUsers(users, query);
  list.replaceChildren(...filtered.map((user) => {
    const card = document.createElement("article");
    card.className = "admin-user";
    const heading = document.createElement("div");
    heading.className = "admin-user__heading";
    const name = document.createElement("strong");
    name.textContent = user.display_name;
    const status = document.createElement("span");
    status.className = "admin-user__status";
    status.textContent = statusLabels[user.status] ?? user.status;
    heading.append(name, status, createReviewAccessControl(user, { onChanged }), createNormativeAccessControl(user, { onChanged }));
    const meta = document.createElement("p");
    meta.className = "admin-user__meta";
    meta.textContent = [user.login, user.email, user.tier].filter(Boolean).join(" · ");
    const detailButton = document.createElement("button");
    detailButton.type = "button";
    detailButton.className = "admin-user__open";
    detailButton.textContent = "Профиль и роль";
    const details = document.createElement("div");
    details.className = "admin-user__details";
    details.hidden = true;
    detailButton.addEventListener("click", async () => {
      if (!details.hidden) {
        details.hidden = true;
        return;
      }
      detailButton.disabled = true;
      details.hidden = false;
      details.textContent = "Загружаем роль…";
      try {
        const response = await getUserRoles(user.user_id);
        const assignments = response.assignments ?? [];
        const roles = response.roles ?? [];
        const profile = {
          ...user,
          ...response.user,
          roles,
          ...roleEditorState(roles, assignments),
        };
        details.replaceChildren(createRoleForm(profile, onChanged));
      } catch (error) {
        details.textContent = error.detail ?? "Не удалось загрузить роль пользователя.";
      } finally {
        detailButton.disabled = false;
      }
    });
    card.append(heading, meta, detailButton, details);
    return card;
  }));
  root.querySelector("[data-admin-users-status]").textContent = filtered.length
    ? `Показано на странице: ${filtered.length}`
    : "Пользователи не найдены.";
}
