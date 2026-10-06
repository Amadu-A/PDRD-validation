// frontend/src/js/features/auth/access.js

/** Интерфейс использует права только для видимости; сервер проверяет каждый запрос. */
export function hasPermission(session, permission) {
  return Boolean(session?.authenticated
    && session.user?.permissions?.includes(permission));
}

export function isAdmin(session) {
  return hasPermission(session, "admin.access");
}

export function accessDescription(session) {
  const roles = session?.user?.roles ?? [];
  if (roles.includes("platform_admin")) return "Администратор";
  if (roles.includes("department_head")) return "Руководитель отдела";
  if (roles.includes("designer")) return "Проектировщик";
  if (session?.authenticated) return "Бесплатный аккаунт";
  return "Гость";
}
