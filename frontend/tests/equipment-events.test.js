// frontend/tests/equipment-events.test.js

/** Проверяет восстановление потока EQ событий и завершение подписки. */
import assert from "node:assert/strict";
import test from "node:test";
import { followEquipmentEvents } from "../src/js/features/analysis/equipment-events.js";
import { rememberGuestAccess } from "../src/js/features/analysis/guest-access.js";

const jobId = "00000000-0000-4000-8000-000000000001";

function eventResponse(text, splitAt = 0) {
  const encoded = new TextEncoder().encode(text);
  return {
    ok: true,
    body: new ReadableStream({
      start(controller) {
        if (splitAt) {
          controller.enqueue(encoded.slice(0, splitAt));
          controller.enqueue(encoded.slice(splitAt));
        } else {
          controller.enqueue(encoded);
        }
        controller.close();
      },
    }),
  };
}

test("EQ SSE повторяет разорванное соединение с Last-Event-ID и гостевым заголовком", async () => {
  const token = "a".repeat(32);
  rememberGuestAccess(jobId, token, new Date(Date.now() + 60_000).toISOString());
  const calls = [];
  const events = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return calls.length === 1
      ? eventResponse('id: 1\r\nevent: equipment_search\r\ndata: {"type":"started","message":"Запуск"}\r\n\r\n', 6)
      : eventResponse('id: 2\nevent: equipment_search\ndata: {"type":"completed","message":"Готово"}\n\n');
  };

  await followEquipmentEvents(jobId, {
    onEvent: (event) => events.push(event.type),
    signal: new AbortController().signal,
    fetchImpl,
    retryMilliseconds: 0,
  });

  assert.deepEqual(events, ["started", "completed"]);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.headers["X-PDRD-Analysis-Access"], token);
  assert.equal(calls[1].options.headers["Last-Event-ID"], "1");
  assert.equal(calls[1].url, "/api/v1/analyses/" + jobId + "/equipment-search/events");
});

test("EQ SSE не запускает запросы после отмены", async () => {
  const abort = new AbortController();
  abort.abort();
  let calls = 0;
  await followEquipmentEvents(jobId, {
    onEvent: () => {},
    signal: abort.signal,
    fetchImpl: async () => { calls++; throw new Error("unexpected"); },
  });
  assert.equal(calls, 0);
});
