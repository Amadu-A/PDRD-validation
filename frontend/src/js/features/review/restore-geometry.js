// frontend/src/js/features/review/restore-geometry.js

/** Сохранённая геометрия Review принадлежит находке и физическому листу, а не порядку DOM-рамок. */

const KEYS = ["x_min", "y_min", "x_max", "y_max"];
const key = (box) => KEYS.map((name) => box[name]).join(":");

/** Проверяет весь набор до изменения DOM; повреждённые координаты не исправляет молча. */
function checkedBoxes(boxes) {
  if (!Array.isArray(boxes)) throw new Error("Сервер вернул неверную геометрию Review.");
  return boxes.map((box) => {
    if (!box || KEYS.some((name) => typeof box[name] !== "number"
        || !Number.isFinite(box[name]) || box[name] < 0 || box[name] > 1000)
        || box.x_min >= box.x_max || box.y_min >= box.y_max) {
      throw new Error("Сервер вернул неверную геометрию Review.");
    }
    return Object.fromEntries(KEYS.map((name) => [name, box[name]]));
  }).filter((box, index, all) => all.findIndex((other) => key(other) === key(box)) === index);
}

/** Вторичные доказательства остаются доступными для чтения; правятся только основные области. */
export function restoredReviewGeometry(entry, row) {
  const proposed = checkedBoxes((row.proposed_regions ?? []).map((region) => region.bbox));
  const evidence = checkedBoxes((row.evidence_locations ?? [])
    .filter((location) => location.page === entry.pageNumber)
    .flatMap((location) => (location.proposed_regions ?? []).map((region) => region.bbox)));
  if (entry.secondaryEvidence) return { boxes: evidence, editableCount: 0 };
  const primary = checkedBoxes(row.display_regions ?? proposed);
  const boxes = [...primary];
  if (entry.documentEvidence) {
    const sources = checkedBoxes((row.document_context_basis_sources ?? [])
      .filter((source) => source.page === entry.pageNumber)
      .flatMap((source) => (source.visual_regions ?? []).map((region) => region.bbox)));
    // Ранний снимок сохранял только четыре основные области и не включал
    // собственные области дополнительных D-доказательств этой страницы.
    const legacy = !evidence.length && !sources.length && proposed.length === 4
      ? checkedBoxes(entry.boxes.slice(4)) : [];
    const seen = new Set([...proposed, ...primary].map(key));
    for (const box of [...evidence, ...sources, ...legacy]) if (!seen.has(key(box))) {
      seen.add(key(box));
      boxes.push(box);
    }
  }
  return { boxes, editableCount: primary.length };
}
