// frontend/src/js/features/review/sync.js

/**
 * Последовательное серверное сохранение, CAS и явное восстановление Review.
 * Не содержит DOM. После ошибки очередь сохраняется и не повторяется сама.
 */

import { reviewCommands } from "./commands.js";

/** Одна очередь относится к одному заданию и переживает смену отображаемого отчёта. */
export function createReviewSync({ jobId, api, snapshot, hydrate, onStatus = () => {} }) {
  let observed = [];
  let revision = null;
  let initialized = false;
  let saving = null;
  let failed = false;
  let conflict = false;
  let detached = false;
  let serverSession = null;
  const queue = [];
  const emit = (value) => { if (!detached) onStatus(value); };

  async function start() {
    emit({ mode: "loading", pending: 0 });
    try {
      const config = await api.config();
      if (detached) return;
      if (!config.enabled) {
        emit({ mode: "local", pending: 0 });
        return;
      }
      const session = await api.open(jobId);
      if (detached) return;
      if (session.job_id !== jobId || !Number.isSafeInteger(session.revision)) {
        throw new Error("Сервер вернул снимок другого задания или неверную ревизию.");
      }
      hydrate(session);
      serverSession = session;
      revision = session.revision;
      observed = snapshot();
      initialized = true;
      emit({ mode: "saved", revision, pending: 0, session: serverSession });
    } catch (error) {
      failed = true;
      emit({ mode: "error", pending: 0, error });
    }
  }

  function changed() {
    if (!initialized || failed || detached) return;
    const current = snapshot();
    queue.push(...reviewCommands(observed, current));
    observed = current;
    if (queue.length) {
      emit({ mode: "saving", revision, pending: queue.length });
      void flush();
    }
  }

  async function flush() {
    if (saving || failed || !initialized) return saving;
    saving = (async () => {
      while (queue.length && !failed) {
        try {
          const response = await api.command(jobId, { ...queue[0], expected_revision: revision });
          if (response.job_id !== jobId || !Number.isSafeInteger(response.revision) || response.revision < revision) {
            throw new Error("Сервер вернул некорректную ревизию Review.");
          }
          revision = response.revision;
          serverSession = response;
          queue.shift();
          emit({ mode: queue.length ? "saving" : "saved", revision, pending: queue.length, session: serverSession });
        } catch (error) {
          failed = true;
          conflict = error.status === 409;
          emit({ mode: conflict ? "conflict" : "error", revision, pending: queue.length, error });
        }
      }
    })();
    try { await saving; } finally { saving = null; }
  }

  return {
    start, changed,
    /** Подтверждение области и утверждение проходят через ту же очередь CAS. */
    async run(command) {
      await saving;
      if (!initialized || failed || detached || queue.length) throw new Error("Сначала сохраните или восстановите Review.");
      queue.push(command);
      emit({ mode: "saving", revision, pending: queue.length, session: serverSession });
      await flush();
      if (failed || queue.length) throw new Error("Команда не подтверждена сервером. Восстановите Review.");
      return serverSession;
    },
    get session() { return serverSession; },
    /** Повторяет ту же CAS-команду; потерянный ответ не приводит к дублированию записи. */
    async retry() {
      if (conflict || !initialized) return;
      await saving;
      failed = false;
      emit({ mode: "saving", revision, pending: queue.length });
      await flush();
    },
    get pending() { return queue.length; },
    /** Старая очередь завершает записи, но больше не обновляет UI нового задания. */
    detach() { detached = true; },
    async settled() { await saving; },
  };
}
