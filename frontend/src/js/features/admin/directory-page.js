// frontend/src/js/features/admin/directory-page.js

/** Показывает разделы исходного каталога Knowledge без создания отдельного справочника. */
import { listSectionCatalog } from "./api.js";

export function bindDirectoryPage(root) {
  const list = root.querySelector("[data-directory-departments]");
  const status = root.querySelector("[data-directory-status]");
  const reload = root.querySelector("[data-directory-reload]");
  let requestId = 0;

  /** Обновляет названия напрямую из сервиса, который обслуживает главную страницу. */
  async function load() {
    const current = ++requestId;
    status.textContent = "Загружаем разделы…";
    try {
      const sections = await listSectionCatalog();
      if (current !== requestId) return;
      list.replaceChildren(...sections.map((entry) => {
        const item = document.createElement("li");
        item.textContent = entry.name;
        return item;
      }));
      status.textContent = sections.length ? "Отметьте доступные разделы в карточке пользователя." : "Создайте разделы на главной странице.";
    } catch (error) {
      if (current === requestId) status.textContent = error.detail ?? error.message ?? "Не удалось загрузить разделы.";
    }
  }
  reload?.addEventListener("click", () => { void load(); });
  return { load };
}
