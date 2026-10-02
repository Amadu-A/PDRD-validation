// frontend/src/js/features/admin/directory-page.js

/** Экран создания организаций и отделов через фасад admin-service. */
import { createDepartment, createOrganization } from "./api.js";
import { allDepartments, allOrganizations } from "./directory.js";

export function bindDirectoryPage(root) {
  const organization = root.querySelector("[data-directory-organization]");
  const organizationForm = root.querySelector("[data-create-organization]");
  const departmentForm = root.querySelector("[data-create-department]");
  const departmentList = root.querySelector("[data-directory-departments]");
  const status = root.querySelector("[data-directory-status]");
  let departmentRequest = 0;

  async function loadDepartments() {
    const request = ++departmentRequest;
    departmentList.replaceChildren();
    if (!organization.value) {
      departmentForm.querySelector('button[type="submit"]').disabled = true;
      return;
    }
    departmentForm.querySelector('button[type="submit"]').disabled = false;
    try {
      const departments = await allDepartments(organization.value);
      if (request !== departmentRequest) return;
      departmentList.replaceChildren(...departments.map((entry) => {
        const item = document.createElement("li");
        item.textContent = `${entry.name}${entry.active ? "" : " · неактивен"}`;
        return item;
      }));
      if (!departments.length) status.textContent = "В этой организации пока нет отделов.";
    } catch (error) {
      if (request === departmentRequest) status.textContent =
        error.detail ?? error.message ?? "Не удалось загрузить отделы.";
    }
  }

  async function load(preferred = organization.value) {
    status.textContent = "Загружаем организации…";
    try {
      const organizations = (await allOrganizations()).filter((entry) => entry.active);
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Выберите организацию";
      organization.replaceChildren(placeholder, ...organizations.map((entry) => {
        const item = document.createElement("option");
        item.value = entry.organization_id;
        item.textContent = entry.name;
        return item;
      }));
      organization.value = organizations.some((entry) => entry.organization_id === preferred)
        ? preferred : "";
      status.textContent = organizations.length ? "" : "Пока нет организаций.";
      await loadDepartments();
    } catch (error) {
      status.textContent = error.detail ?? error.message ?? "Не удалось загрузить организации.";
    }
  }

  organization.addEventListener("change", () => { status.textContent = ""; void loadDepartments(); });
  organizationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = organizationForm.elements.namedItem("name");
    const name = field.value.trim();
    if (!name) return;
    const button = organizationForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const created = await createOrganization(name);
      field.value = "";
      await load(created.organization_id);
      status.textContent = "Организация создана.";
    } catch (error) {
      status.textContent = error.detail ?? error.message ?? "Не удалось создать организацию.";
    } finally {
      button.disabled = false;
    }
  });
  departmentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!organization.value) return;
    const field = departmentForm.elements.namedItem("name");
    const name = field.value.trim();
    if (!name) return;
    const button = departmentForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await createDepartment(organization.value, name);
      field.value = "";
      await loadDepartments();
      status.textContent = "Отдел создан.";
    } catch (error) {
      status.textContent = error.detail ?? error.message ?? "Не удалось создать отдел.";
    } finally {
      button.disabled = false;
    }
  });
  return { load };
}
