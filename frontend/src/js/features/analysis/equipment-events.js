// frontend/src/js/features/analysis/equipment-events.js

/** Читает EQ SSE с гостевым правом в заголовке и восстанавливает Last-Event-ID. */
import { guestAccessHeaders } from "./guest-access.js";

function pause(signal, milliseconds) {
  return new Promise((resolve) => {
    if (signal.aborted) { resolve(); return; }
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function parseFrame(frame) {
  const lines = frame.split("\n");
  const id = lines.find((line) => line.startsWith("id:"))?.slice(3).trim();
  const data = lines.filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim()).join("\n");
  if (!data) return null;
  try {
    const payload = JSON.parse(data);
    return { id: /^[1-9]\d*$/.test(id || "") ? id : null, payload };
  } catch {
    return null;
  }
}

export async function followEquipmentEvents(
  jobId,
  { onEvent, signal, fetchImpl = globalThis.fetch, retryMilliseconds = 1000 },
) {
  /** Повторяет соединение после разрыва и прекращает его при завершении анализа. */
  let lastId = "";
  const url = "/api/v1/analyses/" + encodeURIComponent(jobId)
    + "/equipment-search/events";
  while (!signal.aborted) {
    try {
      const headers = {
        Accept: "text/event-stream",
        ...guestAccessHeaders(jobId),
      };
      if (lastId) headers["Last-Event-ID"] = lastId;
      const response = await fetchImpl(url, {
        headers,
        credentials: "same-origin",
        cache: "no-store",
        signal,
      });
      if (!response.ok || !response.body) throw new Error("EQ SSE недоступен");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r/g, "");
        while (buffer.includes("\n\n")) {
          const index = buffer.indexOf("\n\n");
          const frame = parseFrame(buffer.slice(0, index));
          buffer = buffer.slice(index + 2);
          if (!frame) continue;
          if (frame.id && Number(frame.id) > Number(lastId || 0)) {
            lastId = frame.id;
            onEvent(frame.payload);
            if (["completed", "incomplete", "cancelled"].includes(frame.payload.type)) {
              return;
            }
          }
        }
        if (buffer.length > 10000) buffer = "";
      }
    } catch {
      if (signal.aborted) return;
    }
    await pause(signal, retryMilliseconds);
  }
}
