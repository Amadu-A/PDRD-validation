// frontend/src/js/features/experience/page.js

/** Настоящий каталог серверного Review и явно обозначенная локальная демонстрация. */
import { createExperienceApi } from "./api.js";
import { mountExperienceCatalog } from "./server-page.js";
import { isAdmin } from "../auth/access.js";
import { refreshSession } from "../auth/session.js";

const api = createExperienceApi();
const notice = document.querySelector("[data-experience-notice]");
try {
  const config = await api.config();
  if (typeof config?.enabled !== "boolean") throw new Error("Сервер не подтвердил режим каталога. Обновите страницу.");
  if (config.enabled) {
    const session = await refreshSession();
    if (!isAdmin(session)) {
      document.querySelector(".experience-page").dataset.accessDenied = "true";
      if (notice) notice.textContent = "Каталог Experience и версии доступны только администратору PDRD.";
    } else await mountExperienceCatalog({ api });
  }
  else {
    document.querySelectorAll?.("[data-experience-server-only]").forEach((node) => { node.hidden = true; });
    const save = document.querySelector("[data-experience-edit-save]");
    if (save) save.textContent = "Применить локально";
    const caption = document.querySelector("[data-experience-caption]");
    if (caption) caption.textContent = "Демонстрационные записи Experience";
    if (notice) notice.textContent = "Локальная демонстрация: вымышленные примеры и правки только в этой вкладке. Серверный каталог Experience пока недоступен.";
    await import("./demo-page.js");
  }
} catch (error) {
  document.querySelector(".experience-page").dataset.accessDenied = "true";
  if (notice) { notice.textContent = error.detail ?? error.message; notice.setAttribute("role", "alert"); }
}
