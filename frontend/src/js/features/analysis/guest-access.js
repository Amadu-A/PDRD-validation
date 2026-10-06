// frontend/src/js/features/analysis/guest-access.js

/** Хранит временное право на одно гостевое задание до срока, заданного сервером. */
const JOB_ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const TOKEN = /^[A-Za-z0-9_-]{20,256}$/;
const STORAGE_PREFIX = "pdrd.analysis.guest.v1.";
const inMemory = new Map();

function storage() {
  try { return globalThis.sessionStorage ?? null; } catch { return null; }
}

function key(jobId) { return `${STORAGE_PREFIX}${jobId}`; }

function isValid(record, allowUntimed = false) {
  return Boolean(record && TOKEN.test(record.token)
    && ((allowUntimed && record.expiresAt === null)
      || (Number.isFinite(Date.parse(record.expiresAt))
        && Date.parse(record.expiresAt) > Date.now())));
}

function forget(jobId) {
  inMemory.delete(jobId);
  try { storage()?.removeItem(key(jobId)); } catch { /* Браузер может запретить хранилище. */ }
}

/** Запоминает только token с действительным временем истечения из POST ответа. */
export function rememberGuestAccess(jobId, token, expiresAt) {
  if (!JOB_ID.test(jobId) || !TOKEN.test(token)
    || !Number.isFinite(Date.parse(expiresAt)) || Date.parse(expiresAt) <= Date.now()) {
    throw new Error("API Gateway вернул недействительный срок гостевого доступа.");
  }
  const record = { token, expiresAt };
  inMemory.set(jobId, record);
  try { storage()?.setItem(key(jobId), JSON.stringify(record)); } catch { /* Память вкладки остаётся доступна. */ }
  return record;
}

/** У авторизованного пользователя отдельный guest token отсутствует. */
export function rememberAcceptedAnalysis(response) {
  if (response.access_token == null && response.access_expires_at == null) return null;
  return rememberGuestAccess(response.job_id, response.access_token, response.access_expires_at);
}

export function guestAccessFor(jobId) {
  if (!JOB_ID.test(jobId)) return null;
  let record = inMemory.get(jobId);
  const fromMemory = Boolean(record);
  if (!record) {
    try { record = JSON.parse(storage()?.getItem(key(jobId)) ?? "null"); } catch { record = null; }
  }
  if (!isValid(record, fromMemory)) {
    forget(jobId);
    return null;
  }
  inMemory.set(jobId, record);
  return record;
}

/** Bearer token выдаётся только HTTP клиенту соответствующего задания. */
export function guestAccessHeaders(jobId) {
  const token = guestAccessFor(jobId)?.token;
  return token ? { "X-PDRD-Analysis-Access": token } : {};
}

/** Ссылка для повторного открытия содержит серверный срок и случайный token. */
export function temporaryAnalysisLink(jobId, location = window.location) {
  const access = guestAccessFor(jobId);
  if (!access?.expiresAt) return null;
  const url = new URL("/", location.origin);
  const fragment = new URLSearchParams();
  fragment.set("job_id", jobId);
  fragment.set("access_token", access.token);
  fragment.set("access_expires_at", access.expiresAt);
  url.hash = fragment.toString();
  return url.toString();
}

/** При переходе по ссылке сразу убирает token из адресной строки браузера. */
export function consumeGuestAccessFromUrl(location, history) {
  const url = new URL(location.href);
  const fragment = new URLSearchParams(url.hash.slice(1));
  const fromFragment = fragment.has("access_token");
  const source = fromFragment ? fragment : url.searchParams;
  const jobId = source.get("job_id") ?? url.searchParams.get("job_id");
  const token = source.get("access_token");
  const expiresAt = source.get("access_expires_at");
  if (token !== null || expiresAt !== null) {
    url.searchParams.delete("access_token");
    url.searchParams.delete("access_expires_at");
    if (fromFragment) {
      if (jobId) url.searchParams.set("job_id", jobId);
      url.hash = "";
    }
    history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }
  if (!jobId || !token || !JOB_ID.test(jobId) || !TOKEN.test(token)) return jobId;
  if (expiresAt) {
    try { rememberGuestAccess(jobId, token, expiresAt); } catch { forget(jobId); }
  } else {
    // Короткий status_url API также работает, но без срока не записывается в storage.
    inMemory.set(jobId, { token, expiresAt: null });
  }
  return jobId;
}
