// frontend/src/js/features/admin/role-assignment.js

/** Последовательно создаёт членство и роль, сохраняя CAS версию прав. */
import { activateMembership, changeUserRole } from "./api.js";

export function hasActiveMembership(memberships, scope) {
  return memberships.some((entry) => entry.active
    && entry.organization_id === scope.organization_id
    && entry.department_id === scope.department_id);
}

export async function saveRoleWithMembership({ user, role, scope, memberships }, {
  activate = activateMembership, changeRole = changeUserRole,
} = {}) {
  let version = user.authorization_version;
  let membershipChanged = false;
  if (role === "department_head" && !hasActiveMembership(memberships, scope)) {
    const response = await activate(user.user_id, scope.organization_id,
      scope.department_id, version);
    membershipChanged = true;
    version = response.authorization_version;
    if (!Number.isSafeInteger(version) || version <= user.authorization_version) {
      const error = new Error("Членство изменено, но сервис не вернул новую версию прав. Откройте профиль заново.");
      error.partial = true;
      throw error;
    }
  }
  try {
    return await changeRole(user.user_id, role, scope, version);
  } catch (error) {
    if (!membershipChanged) throw error;
    const partial = new Error(
      `Членство создано, но роль не изменена. Откройте профиль заново. ${error.detail ?? error.message ?? ""}`,
    );
    partial.partial = true;
    throw partial;
  }
}
