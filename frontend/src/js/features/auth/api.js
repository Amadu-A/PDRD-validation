// frontend/src/js/features/auth/api.js

/** Публичный браузерный контракт auth-service через тот же API Gateway. */
import { ApiError, fetchJson } from "../../api.js";

const ROOT = "/api/v1/auth";
let csrfToken = "";

export function authenticatedRequest(path, method = "GET", body) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken && method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  return fetchJson(path, {
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

/** Не отправляет пароль со страницы без HTTPS; проверка сервера остаётся обязательной. */
function request(path, method = "GET", body) {
  if (body?.password !== undefined && globalThis.location
      && globalThis.location.protocol !== "https:") {
    return Promise.reject(new ApiError(
      403, "Вход и регистрация доступны только по HTTPS. Откройте защищённый адрес PDRD.",
    ));
  }
  return authenticatedRequest(`${ROOT}${path}`, method, body);
}

/** Токен берётся только из подтверждённого сервером ответа текущей сессии. */
export function setCsrfToken(token) {
  csrfToken = typeof token === "string" ? token : "";
}

export const readSession = () => request("/session");
export const login = (loginName, password) => request("/login", "POST", {
  login: loginName, password,
});
export const register = (displayName, email, password) => request("/register", "POST", {
  display_name: displayName, email, password,
});
export const verifyEmail = (token) => request("/verify-email", "POST", { token });
export const logout = () => request("/logout", "POST");
export const logoutAll = () => request("/logout-all", "POST");
export const listSessions = () => request("/sessions");
export const revokeSession = (sessionId) => request(
  `/sessions/${encodeURIComponent(sessionId)}`, "DELETE",
);
