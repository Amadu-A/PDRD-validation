// frontend/src/js/features/review/manual-records.js

/**
 * Канонические клиентские данные Gold-находок для будущего серверного review.
 * Не знает DOM, PDF-renderer и API; координаты привязаны к конкретному листу.
 */

function copyBox(box) {
  const keys = ["x_min", "y_min", "x_max", "y_max"];
  if (!box || keys.some((key) => !Number.isFinite(box[key]))) {
    throw new Error("Координаты ручного замечания не определены.");
  }
  const result = Object.fromEntries(keys.map((key) => [key, box[key]]));
  if (
    result.x_min < 0 || result.y_min < 0
    || result.x_max > 1000 || result.y_max > 1000
    || result.x_min >= result.x_max || result.y_min >= result.y_max
  ) {
    throw new Error("Координаты ручного замечания выходят за границы листа.");
  }
  return result;
}

/** Хранит единственную версию данных для списка и будущего экспорта. */
export function createManualRecords() {
  const records = new Map();

  function add(note, review) {
    if (records.has(note.findingId)) {
      throw new Error("Ручное замечание с таким ID уже существует.");
    }
    if (
      !String(note.findingId ?? "").startsWith("manual:")
      || !Number.isInteger(note.pageNumber) || note.pageNumber < 1
      || review.origin !== "manual"
      || review.findingId !== note.findingId
    ) {
      throw new Error("Неверная идентификация ручного замечания.");
    }
    const record = {
      finding_id: note.findingId,
      origin: "manual",
      page_number: note.pageNumber,
      issue_box: copyBox(note.issueBox),
      callout_box: copyBox(note.calloutBox),
      text: review.text,
      normative_section: review.normativeSection,
      decision: review.decision,
      experience_tag: "gold",
      revision: review.revision,
    };
    records.set(note.findingId, record);
    return {
      ...record,
      issue_box: { ...record.issue_box },
      callout_box: { ...record.callout_box },
    };
  }

  function sync(review) {
    const record = records.get(review.findingId);
    if (!record) {
      return;
    }
    if (review.origin !== "manual" || review.experienceTag !== "gold") {
      throw new Error("Источник Gold-замечания не может быть изменён.");
    }
    record.text = review.text;
    record.normative_section = review.normativeSection;
    record.decision = review.decision;
    record.revision = review.revision;
  }

  function snapshot() {
    return [...records.values()].map((record) => ({
      ...record,
      issue_box: { ...record.issue_box },
      callout_box: { ...record.callout_box },
    }));
  }

  return { add, sync, snapshot };
}