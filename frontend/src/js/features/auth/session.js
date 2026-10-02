// frontend/src/js/features/auth/session.js

/** Хранит только публичное представление сессии в памяти текущей вкладки. */
import { ApiError } from "../../api.js";
import { readSession, setCsrfToken } from "./api.js";

const guest = Object.freeze({ authenticated: false, user: null, session: null });
let current = guest;
const listeners = new Set();
let revision = 0;
let requestSequence = 0;

function publish(next) {
  revision += 1;
  current = next;
  setCsrfToken(next.csrf_token);
  for (const listener of listeners) listener(current);
  return current;
}

export function currentSession() { return current; }

export function subscribeSession(listener) {
  listeners.add(listener);
  listener(current);
  return () => listeners.delete(listener);
}

export function clearSession() { return publish(guest); }

export async function refreshSession() {
  // Ответ старого запроса не должен восстанавливать профиль после выхода.
  const startedAt = revision;
  const requestId = ++requestSequence;
  const isLatest = () => startedAt === revision && requestId === requestSequence;
  try {
    const response = await readSession();
    if (!isLatest()) return current;
    if (!response?.authenticated || !response.user) return publish(guest);
    const user = response.user;
    return publish({
      authenticated: true,
      user: {
        ...user,
        roles: Array.isArray(user.roles) ? user.roles : [],
        permissions: Array.isArray(user.permissions) ? user.permissions : [],
      },
      session: response.session ?? null,
      csrf_token: response.csrf_token ?? "",
    });
  } catch (error) {
    if (!isLatest()) return current;
    if (error instanceof ApiError && error.status === 401) return publish(guest);
    clearSession();
    throw error;
  }
}
