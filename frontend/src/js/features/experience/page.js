// frontend/src/js/features/experience/page.js

/** Настоящий каталог серверного Review и явно обозначенная локальная демонстрация. */
import { createExperienceApi } from "./api.js";
import { mountExperienceCatalog } from "./server-page.js";

const api = createExperienceApi();
const notice = document.querySelector("[data-experience-notice]");
try {
  const config = await api.config();
  if (typeof config?.enabled !== "boolean") throw new Error("Сервер не подтвердил режим каталога. Обновите страницу.");
  if (config.enabled) await mountExperienceCatalog({ api });
  else {
    document.querySelectorAll?.("[data-experience-server-only]").forEach((node) => { node.hidden = true; });
    const save = document.querySelector("[data-experience-edit-save]");
    if (save) save.textContent = "Применить локально";
    const caption = document.querySelector("[data-experience-caption]");
    if (caption) caption.textContent = "Демонстрационные записи Experience";
    if (notice) notice.textContent = "Локальная демонстрация: вымышленные примеры и правки только в этой вкладке. Настоящий каталог доступен в серверном Review на http://127.0.0.1:8081 через SSH-туннель.";
    await import("./demo-page.js");
  }
} catch (error) {
  if (notice) { notice.textContent = error.detail ?? error.message; notice.setAttribute("role", "alert"); }
}
