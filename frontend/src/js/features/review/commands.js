// frontend/src/js/features/review/commands.js

/** Чистое преобразование клиентских изменений в строгие команды Review API. */

const equal = (first, second) => JSON.stringify(first) === JSON.stringify(second);

/** Объединяет решения и геометрию из отдельных UI-адаптеров. */
export function reviewEntries(review, manual) {
  const gold = new Map(manual.map((row) => [row.finding_id, row]));
  return review.map((entry) => {
    const note = gold.get(entry.findingId);
    const location = entry.visualizations?.[0];
    return {
      finding_id: entry.findingId, origin: entry.origin,
      text: entry.text, normative_basis: entry.normativeSection,
      decision: entry.decision,
      reason_category: entry.reasonCategory ?? null, comment: entry.comment ?? "",
      page_number: note?.page_number ?? location?.page_number,
      regions: note ? [note.issue_box] : (location?.proposed_issue_boxes ?? []),
      callout_box: note?.callout_box ?? location?.callout_box ?? null,
    };
  });
}

/** Оригиналы и теги не отправляются; сервер сохраняет собственную provenance. */
export function reviewCommands(previous, current) {
  const before = new Map(previous.map((entry) => [entry.finding_id, entry]));
  const result = [];
  for (const row of current) {
    const earlier = before.get(row.finding_id);
    const identity = { finding_id: row.finding_id };
    let resetsDecision = false;
    if (!earlier) {
      if (row.origin !== "manual") throw new Error("Клиент не может добавлять замечания VLM.");
      result.push({ action: "add", ...identity, page_number: row.page_number,
        text: row.text, normative_basis: row.normative_basis,
        issue_box: row.regions[0], callout_box: row.callout_box });
      resetsDecision = true;
    } else {
      if (row.text !== earlier.text || row.normative_basis !== earlier.normative_basis) {
        result.push({ action: "edit", ...identity, text: row.text, normative_basis: row.normative_basis });
        resetsDecision = true;
      }
      if (!equal(row.regions, earlier.regions) || !equal(row.callout_box, earlier.callout_box)) {
        result.push({ action: "geometry", ...identity, regions: row.regions, callout_box: row.callout_box });
        resetsDecision = true;
      }
    }
    if (row.decision === "pending") {
      if (earlier?.decision !== "pending" && earlier && !resetsDecision) {
        result.push({ action: "reset", ...identity });
      }
    } else if (resetsDecision || row.decision !== earlier?.decision || (
      row.decision === "rejected" && ((row.reason_category ?? null) !== (earlier?.reason_category ?? null)
        || (row.comment ?? "") !== (earlier?.comment ?? ""))
    )) {
      result.push({ action: "decide", ...identity, decision: row.decision,
        ...(row.decision === "rejected" ? { reason_category: row.reason_category, comment: row.comment ?? "" } : {}),
      });
    }
  }
  return result;
}
