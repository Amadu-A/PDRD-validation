// frontend/src/js/features/admin/api.js

/** Админка обращается только к фасаду admin-service через публичный Gateway. */
import { authenticatedRequest } from "../auth/api.js";

const ROOT = "/api/v1/admin";

export async function listUsers({ limit = 50, offset = 0 } = {}) {
  const response = await authenticatedRequest(
    `${ROOT}/users?limit=${limit}&offset=${offset}`,
  );
  if (!Array.isArray(response.items) || !Number.isInteger(response.total)) {
    throw new Error("Некорректный ответ списка пользователей.");
  }
  return response;
}

export function getUserRoles(userId) {
  return authenticatedRequest(`${ROOT}/users/${encodeURIComponent(userId)}/roles`);
}

export function changeUserRole(userId, role, scope, authorizationVersion, sectionIds) {
  return authenticatedRequest(
    `${ROOT}/users/${encodeURIComponent(userId)}/role`,
    "PATCH",
    { role, scope, authorization_version: authorizationVersion, section_ids: sectionIds },
  );
}

/** Справочники и членство изменяются через отдельные контракты admin-service. */
export function listOrganizations({ limit = 100, offset = 0 } = {}) {
  return authenticatedRequest(`${ROOT}/organizations?limit=${limit}&offset=${offset}`);
}

export function createOrganization(name) {
  return authenticatedRequest(`${ROOT}/organizations`, "POST", { name });
}

export function listDepartments(organizationId, { limit = 100, offset = 0 } = {}) {
  return authenticatedRequest(
    `${ROOT}/organizations/${encodeURIComponent(organizationId)}/departments?limit=${limit}&offset=${offset}`,
  );
}

export function createDepartment(organizationId, name) {
  return authenticatedRequest(
    `${ROOT}/organizations/${encodeURIComponent(organizationId)}/departments`, "POST", { name },
  );
}

export function listMemberships(userId) {
  return authenticatedRequest(`${ROOT}/users/${encodeURIComponent(userId)}/memberships`);
}

export function activateMembership(userId, organizationId, departmentId, authorizationVersion) {
  return authenticatedRequest(
    `${ROOT}/users/${encodeURIComponent(userId)}/memberships/${encodeURIComponent(organizationId)}/${encodeURIComponent(departmentId)}`,
    "PUT", { authorization_version: authorizationVersion },
  );
}

export function deactivateMembership(userId, organizationId, departmentId, authorizationVersion) {
  return authenticatedRequest(
    `${ROOT}/users/${encodeURIComponent(userId)}/memberships/${encodeURIComponent(organizationId)}/${encodeURIComponent(departmentId)}`,
    "DELETE", { authorization_version: authorizationVersion },
  );
}


/** Читает тот же каталог разделов, который используется на главной странице. */
export function listSectionCatalog() {
  return authenticatedRequest(`${ROOT}/users/section-catalog`);
}

/** Читает актуальные назначения разделов и их CAS версию. */
export function getUserSections(userId) {
  return authenticatedRequest(`${ROOT}/users/${encodeURIComponent(userId)}/section-access`);
}
