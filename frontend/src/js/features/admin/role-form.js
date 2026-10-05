// frontend/src/js/features/admin/role-form.js

/** Выбор роли пользователя и отдела из проверенного справочника admin-service. */
import { listMemberships } from "./api.js";
import { allDepartments, allOrganizations } from "./directory.js";
import { saveRoleWithMembership } from "./role-assignment.js";

const roles = [
  ["", "Без рабочей роли"],
  ["designer", "Проектировщик"],
  ["department_head", "Руководитель отдела"],
  ["platform_admin", "Администратор платформы"],
];
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

/** Проверяет область выбранной роли и обязательные идентификаторы отдела. */
export function roleScope(role, organizationId = "", departmentId = "") {
  if (role === "designer") return { kind: "own" };
  if (role === "platform_admin") return { kind: "platform" };
  if (role === "department_head") {
    if (!UUID.test(organizationId) || !UUID.test(departmentId)) {
      throw new Error("Для руководителя выберите организацию и отдел.");
    }
    return {
      kind: "department",
      organization_id: organizationId,
      department_id: departmentId,
    };
  }
  return null;
}

function option(value, label) {
  const entry = document.createElement("option");
  entry.value = value;
  entry.textContent = label;
  return entry;
}

function fillSelect(select, placeholder, items, valueKey, preferred = "") {
  select.replaceChildren(option("", placeholder), ...items.map((item) =>
    option(item[valueKey], item.name)));
  select.value = items.some((item) => item[valueKey] === preferred) ? preferred : "";
}

/** Создаёт форму назначения роли с проверками статуса и версии полномочий. */
export function createRoleForm(user, onChanged) {
  const form = document.createElement("form");
  form.className = "admin-role-form";
  const selectLabel = document.createElement("label");
  selectLabel.textContent = "Роль PDRD";
  const select = document.createElement("select");
  select.name = "role";
  for (const [value, label] of roles) {
    const entry = option(value, label);
    select.append(entry);
  }
  select.value = user.currentRole ?? user.roles?.[0] ?? "";
  select.disabled = Boolean(user.roleLocked);
  selectLabel.append(select);

  const departmentFields = document.createElement("div");
  departmentFields.className = "admin-role-form__department";
  const organizationLabel = document.createElement("label");
  organizationLabel.textContent = "Организация";
  const organization = document.createElement("select");
  organization.name = "organization_id";
  organization.disabled = true;
  fillSelect(organization, "Выберите организацию", [], "organization_id");
  organizationLabel.append(organization);
  const departmentLabel = document.createElement("label");
  departmentLabel.textContent = "Отдел";
  const department = document.createElement("select");
  department.name = "department_id";
  department.disabled = true;
  fillSelect(department, "Выберите отдел", [], "department_id");
  departmentLabel.append(department);
  const reload = document.createElement("button");
  reload.type = "button";
  reload.textContent = "Обновить справочник";
  departmentFields.append(organizationLabel, departmentLabel, reload);

  const button = document.createElement("button");
  button.type = "submit";
  button.textContent = "Сохранить роль";
  const message = document.createElement("p");
  message.className = "admin-role-form__message";
  message.setAttribute("role", "status");
  let directoryState = "idle";
  let memberships = [];
  let departmentLoad = 0;
  let staleVersion = false;

  function updateControls() {
    const head = select.value === "department_head";
    departmentFields.hidden = !head;
    button.disabled = Boolean(user.roleLocked) || (user.status && user.status !== "active")
      || staleVersion
      || (head && (directoryState !== "ready" || !organization.value || !department.value));
    if (user.roleLocked) {
      message.textContent = "Назначение из AD меняется в источнике.";
    } else if (user.status && user.status !== "active") {
      message.textContent = "Роли доступны только активным пользователям.";
    } else if (head && directoryState === "loading") {
      message.textContent = "Загружаем организации и отделы…";
    } else if (head && directoryState === "error") {
      message.textContent = "Не удалось загрузить справочник. Повторите запрос.";
    } else if (head && directoryState === "ready" && !organization.value) {
      message.textContent = "Выберите организацию. Если список пуст, создайте её в разделе «Роли и группы».";
    } else if (head && directoryState === "ready" && !department.value) {
      message.textContent = "Выберите отдел. Если список пуст, создайте его в разделе «Роли и группы».";
    } else if (!staleVersion) {
      message.textContent = "";
    }
  }

  async function loadDepartmentOptions(preferred = "") {
    const request = ++departmentLoad;
    department.disabled = true;
    fillSelect(department, "Выберите отдел", [], "department_id");
    directoryState = "loading";
    updateControls();
    if (!organization.value) {
      directoryState = "ready";
      updateControls();
      return;
    }
    try {
      const items = (await allDepartments(organization.value)).filter((item) => item.active);
      if (request !== departmentLoad) return;
      fillSelect(department, "Выберите отдел", items, "department_id", preferred);
      department.disabled = false;
      directoryState = "ready";
    } catch {
      if (request !== departmentLoad) return;
      directoryState = "error";
    }
    updateControls();
  }

  async function loadDirectory() {
    if (directoryState === "loading") return;
    directoryState = "loading";
    organization.disabled = true;
    reload.disabled = true;
    updateControls();
    try {
      const [items, currentMemberships] = await Promise.all([
        allOrganizations(), listMemberships(user.user_id),
      ]);
      memberships = currentMemberships;
      fillSelect(organization, "Выберите организацию", items.filter((item) => item.active),
        "organization_id", user.scope?.organization_id ?? "");
      organization.disabled = false;
      await loadDepartmentOptions(user.scope?.department_id ?? "");
    } catch {
      directoryState = "error";
      updateControls();
    } finally {
      reload.disabled = false;
    }
  }

  select.addEventListener("change", () => {
    updateControls();
    if (select.value === "department_head" && directoryState === "idle") void loadDirectory();
  });
  organization.addEventListener("change", () => { void loadDepartmentOptions(); });
  department.addEventListener("change", updateControls);
  reload.addEventListener("click", () => { void loadDirectory(); });
  form.append(selectLabel, departmentFields, button, message);
  updateControls();
  if (select.value === "department_head" && !user.roleLocked && (!user.status || user.status === "active")) {
    void loadDirectory();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    message.textContent = "Сохраняем…";
    try {
      const scope = roleScope(select.value, organization.value, department.value);
      await saveRoleWithMembership({
        user, role: select.value || null, scope, memberships,
      });
      message.textContent = "Роль обновлена.";
      await onChanged();
    } catch (error) {
      staleVersion = Boolean(error.partial || error.status === 409);
      message.textContent = error.detail ?? error.message ?? "Не удалось изменить роль.";
    } finally {
      button.disabled = staleVersion || Boolean(user.roleLocked) || (user.status && user.status !== "active")
        || (select.value === "department_head"
          && (directoryState !== "ready" || !organization.value || !department.value));
    }
  });
  return form;
}
