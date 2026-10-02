// frontend/src/js/features/admin/directory.js

/** Читает весь ограниченно пагинированный справочник для выбора области роли. */
import { listDepartments, listOrganizations } from "./api.js";

async function allPages(load) {
  const items = [];
  let offset = 0;
  for (;;) {
    const page = await load({ limit: 100, offset });
    if (!Array.isArray(page.items) || !Number.isSafeInteger(page.total)) {
      throw new Error("Справочник вернул некорректную страницу.");
    }
    items.push(...page.items);
    if (items.length >= page.total) return items;
    if (!page.items.length) throw new Error("Не удалось загрузить полный справочник.");
    offset += page.items.length;
  }
}

export const allOrganizations = () => allPages(listOrganizations);
export const allDepartments = (organizationId) => allPages(
  (page) => listDepartments(organizationId, page),
);
