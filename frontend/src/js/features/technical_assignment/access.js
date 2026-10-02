// frontend/src/js/features/technical_assignment/access.js

/** Держит HMAC-доступ к одному подготовленному ТЗ до срока, заданного сервером. */
const PREFIX = "pdrd.technical-assignment.access.v1.";
const records = new Map();
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

function storage() {
  try { return globalThis.sessionStorage ?? null; } catch { return null; }
}

function valid(record) {
  return Boolean(record && typeof record.token === "string" && record.token.length > 0
    && record.token.length <= 256 && /^[\x21-\x7e]+$/.test(record.token)
    && Number.isFinite(Date.parse(record.expiresAt))
    && Date.parse(record.expiresAt) > Date.now());
}

export function forgetTechnicalAssignmentAccess(id) {
  if (!UUID.test(id)) return;
  records.delete(id);
  try { storage()?.removeItem(`${PREFIX}${id}`); } catch { /* Запрет storage не мешает очистке памяти. */ }
}

/** При legacy mode оба поля отсутствуют; в auth mode оба обязательны. */
export function rememberPreparedTechnicalAssignment(payload) {
  const id = payload.technical_assignment_id;
  if (payload.access_token == null && payload.access_expires_at == null) return null;
  const record = { token: payload.access_token, expiresAt: payload.access_expires_at };
  if (!UUID.test(id) || !valid(record)) {
    throw new Error("Сервер не вернул действующий доступ к подготовленному ТЗ.");
  }
  records.set(id, record);
  try { storage()?.setItem(`${PREFIX}${id}`, JSON.stringify(record)); } catch { /* Вкладка продолжит работу в памяти. */ }
  return record;
}

export function technicalAssignmentAccessFor(id) {
  if (!UUID.test(id)) return null;
  let record = records.get(id);
  if (!record) {
    try { record = JSON.parse(storage()?.getItem(`${PREFIX}${id}`) ?? "null"); } catch { record = null; }
  }
  if (!valid(record)) {
    forgetTechnicalAssignmentAccess(id);
    return null;
  }
  records.set(id, record);
  return record.token;
}

export function technicalAssignmentAccessHeaders(id) {
  const token = technicalAssignmentAccessFor(id);
  return token ? { "X-PDRD-Technical-Assignment-Access": token } : {};
}
