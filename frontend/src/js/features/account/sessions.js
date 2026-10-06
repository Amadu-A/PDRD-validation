// frontend/src/js/features/account/sessions.js

/** Список устройств, отзыв отдельной и всех серверных сессий. */
import { listSessions, logoutAll, revokeSession } from "../auth/api.js";
import { clearSession } from "../auth/session.js";

const renderVersions = new WeakMap();

function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Дата неизвестна" : date.toLocaleString("ru-RU");
}

export async function renderSessions(root, current, { navigate = (url) => { window.location.href = url; } } = {}) {
  // Каждая смена пользователя отменяет право старого ответа менять список устройств.
  const version = (renderVersions.get(root) ?? 0) + 1;
  renderVersions.set(root, version);
  const isCurrent = () => renderVersions.get(root) === version;
  const list = root.querySelector("[data-session-list]");
  const summary = root.querySelector("[data-session-summary]");
  const status = root.querySelector("[data-session-status]");
  const logoutAllButton = root.querySelector("[data-account-logout-all]");
  list.replaceChildren();
  logoutAllButton.onclick = null;
  logoutAllButton.disabled = false;
  logoutAllButton.hidden = !current.authenticated;
  if (!current.authenticated) {
    summary.textContent = "После входа здесь будут активные сессии и управление устройствами.";
    status.textContent = "";
    return;
  }
  summary.textContent = "Завершите отдельную сессию или выйдите сразу со всех устройств.";
  status.textContent = "Загружаем активные сессии…";
  logoutAllButton.onclick = async () => {
    if (!isCurrent()) return;
    logoutAllButton.disabled = true;
    try {
      await logoutAll();
      if (!isCurrent()) return;
      clearSession();
      navigate("/");
    } catch (error) {
      if (!isCurrent()) return;
      status.textContent = error.detail ?? "Не удалось завершить все сессии.";
      logoutAllButton.disabled = false;
    }
  };
  try {
    const response = await listSessions();
    if (!isCurrent()) return;
    const items = Array.isArray(response) ? response
      : response.sessions ?? response.items ?? [];
    for (const session of items) {
      const item = document.createElement("li");
      const text = document.createElement("span");
      const id = session.session_id ?? session.id;
      text.textContent = `${id === current.session?.session_id ? "Текущая сессия · " : ""}С ${dateLabel(session.created_at)} · до ${dateLabel(session.absolute_expires_at)}`;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Завершить";
      button.addEventListener("click", async () => {
        if (!isCurrent()) return;
        button.disabled = true;
        try {
          await revokeSession(id);
          if (!isCurrent()) return;
          if (id === current.session?.session_id) {
            clearSession();
            navigate("/");
          } else await renderSessions(root, current, { navigate });
        } catch (error) {
          if (!isCurrent()) return;
          status.textContent = error.detail ?? "Не удалось завершить сессию.";
          button.disabled = false;
        }
      });
      item.append(text, button);
      list.append(item);
    }
    status.textContent = items.length ? "" : "Активных сессий нет.";
  } catch (error) {
    if (!isCurrent()) return;
    status.textContent = error.detail ?? "Не удалось загрузить сессии.";
  }
}
