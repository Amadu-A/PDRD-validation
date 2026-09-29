// frontend/src/js/features/experience/api.js

/** Сетевой адаптер настоящего каталога без браузерного actor или служебного ключа. */
import { ApiError, fetchJson } from "../../api.js";

const BASE = "/api/v1/experience";
const encode = encodeURIComponent;

/** Пустой фильтр не отправляется серверу; false остаётся явным значением. */
function parameters(criteria = {}) {
  const values = Object.entries(criteria).filter(([, value]) => value !== "" && value != null);
  return new URLSearchParams(values).toString();
}

/** Проверяет ZIP, чтобы HTTP-ошибка или HTML не скачались как архив примеров. */
async function archive(criteria) {
  const response = await fetch(`${BASE}/export?${parameters(criteria)}`, { cache: "no-store" });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(response.status, typeof payload.detail === "string" ? payload.detail : "Не удалось выгрузить Experience.");
  }
  const blob = await response.blob();
  const signature = new Uint8Array(await blob.slice(0, 2).arrayBuffer());
  if (response.headers.get("content-type")?.split(";")[0] !== "application/zip"
      || signature[0] !== 80 || signature[1] !== 75) throw new Error("Сервер вернул файл неверного формата.");
  return { blob, filename: "experience.zip" };
}

/** Одинаковый адаптер используется страницей каталога и переносом из Review. */
export function createExperienceApi() {
  const send = (url, method, body) => fetchJson(url, { method, cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return {
    config: () => fetchJson("/api/v1/review/config", { cache: "no-store" }),
    list: (criteria) => fetchJson(`${BASE}?${parameters(criteria)}`, { cache: "no-store" }),
    get: (id) => fetchJson(`${BASE}/${encode(id)}`, { cache: "no-store" }),
    update: (id, revision, fields) => send(`${BASE}/${encode(id)}`, "PATCH", { expected_revision: revision, fields }),
    deactivate: (id, revision) => fetchJson(`${BASE}/${encode(id)}?expected_revision=${revision}`, { method: "DELETE" }),
    deleteSelection: (items) => send(`${BASE}/delete-selection`, "POST", { items }),
    versions: () => fetchJson("/api/v1/experience-versions", { cache: "no-store" }),
    version: (id) => fetchJson(`/api/v1/experience-versions/${encode(id)}`, { cache: "no-store" }),
    createVersion: (fields) => send("/api/v1/experience-versions", "POST", fields),
    renameVersion: (id, revision, name) => send(`/api/v1/experience-versions/${encode(id)}`, "PATCH", { expected_revision: revision, name }),
    deleteVersion: (id, revision) => send(`/api/v1/experience-versions/${encode(id)}/delete`, "POST", { expected_revision: revision }),
    applyVersion: (id, revision) => send(`/api/v1/experience-versions/${encode(id)}/apply`, "POST", { expected_revision: revision }),
    capture: (jobId, revision) => send(`${BASE}/capture/${encode(jobId)}`, "POST", { expected_revision: revision }),
    imageUrl: (id, index = 0) => `${BASE}/${encode(id)}/crops/${index}`,
    history: (id) => fetchJson(`${BASE}/${encode(id)}/history`, { cache: "no-store" }),
    export: archive,
  };
}
