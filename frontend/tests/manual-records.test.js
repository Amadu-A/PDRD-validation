// frontend/tests/manual-records.test.js

/** Каноническая Gold-запись для последующего серверного экспорта. */

import assert from "node:assert/strict";
import test from "node:test";
import { createManualRecords } from "../src/js/features/review/manual-records.js";

const issueBox = { x_min: 10, y_min: 20, x_max: 300, y_max: 400 };
const calloutBox = { x_min: 500, y_min: 450, x_max: 800, y_max: 700 };

function fixture() {
  return {
    note: {
      findingId: "manual:one",
      pageNumber: 22,
      issueBox: { ...issueBox },
      calloutBox: { ...calloutBox },
    },
    review: {
      findingId: "manual:one",
      origin: "manual",
      text: "Замечание пользователя",
      normativeSection: "СП 123, п. 4",
      decision: "pending",
      experienceTag: "gold",
      revision: 0,
    },
  };
}

test("Gold-запись включает обе области и текст с нормативом и страницей", () => {
  const records = createManualRecords();
  const { note, review } = fixture();
  records.add(note, review);
  assert.deepEqual(records.snapshot(), [{
    finding_id: "manual:one",
    origin: "manual",
    page_number: 22,
    issue_box: issueBox,
    callout_box: calloutBox,
    text: "Замечание пользователя",
    normative_section: "СП 123, п. 4",
    decision: "pending",
    experience_tag: "gold",
    revision: 0,
  }]);
});

test("изменение решения и текста отражается в единственной записи", () => {
  const records = createManualRecords();
  const { note, review } = fixture();
  records.add(note, review);
  records.sync({ ...review, text: "Уточнено", decision: "accepted", revision: 2 });
  const result = records.snapshot();
  assert.equal(result[0].text, "Уточнено");
  assert.equal(result[0].decision, "accepted");
  assert.equal(result[0].experience_tag, "gold");
  assert.equal(result[0].revision, 2);
  result[0].issue_box.x_min = 999;
  assert.equal(records.snapshot()[0].issue_box.x_min, 10);
});

test("не допускает неверную геометрию, иной источник и дубликаты", () => {
  const records = createManualRecords();
  const { note, review } = fixture();
  assert.throws(() => records.add({ ...note, pageNumber: 0 }, review));
  assert.throws(() => records.add({ ...note, issueBox: { ...issueBox, x_max: 1200 } }, review));
  assert.throws(() => records.add({ ...note, calloutBox: { ...calloutBox, y_max: 450 } }, review));
  assert.throws(() => records.add(note, { ...review, origin: "vlm" }));
  records.add(note, review);
  assert.throws(() => records.add(note, review));
  assert.throws(() => records.sync({ ...review, origin: "vlm" }));
});

test("новые и неизвестные записи не меняют существующие", () => {
  const records = createManualRecords();
  const { note, review } = fixture();
  records.add(note, review);
  records.sync({ ...review, findingId: "manual:unknown", decision: "rejected" });
  assert.equal(records.snapshot()[0].decision, "pending");
  assert.equal(records.snapshot().length, 1);
});

test("обе области обновляются атомарно и не меняют лист, источник и текст", () => {
  const records = createManualRecords();
  const { note, review } = fixture();
  records.add(note, review);
  const nextIssue = { x_min: 100, y_min: 150, x_max: 400, y_max: 500 };
  const nextCallout = { x_min: 600, y_min: 500, x_max: 900, y_max: 800 };
  const before = records.snapshot();
  assert.throws(() => records.updateGeometry(note.findingId, nextIssue, { ...nextCallout, y_max: 1200 }));
  assert.deepEqual(records.snapshot(), before);
  assert.equal(records.updateGeometry(note.findingId, nextIssue, nextCallout), true);
  assert.equal(records.updateGeometry(note.findingId, nextIssue, nextCallout), false);
  nextIssue.x_min = 999;
  const snapshot = records.snapshot()[0];
  assert.equal(snapshot.issue_box.x_min, 100);
  assert.equal(snapshot.page_number, 22);
  assert.equal(snapshot.experience_tag, "gold");
  assert.equal(snapshot.text, review.text);
  assert.throws(() => records.updateGeometry("unknown", issueBox, calloutBox));
  records.remove(note.findingId);
  assert.deepEqual(records.snapshot(), []);
});
