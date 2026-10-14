// frontend/tests/equipment-provenance.test.js

/** Проверяет отдельную идентичность сохранённого EQ-источника. */
import assert from "node:assert/strict";
import test from "node:test";
import {
  findingSourceDescription,
  findingSourceKinds,
  equipmentSnapshotUrl,
} from "../src/js/features/analysis/finding-provenance.js";

test("документация производителя обозначается как EQ, не как норматив", () => {
  const finding = {
    basis_sources: [],
    equipment_documentation_basis_sources: [{
      source_id: "EQ-snapshot-1",
      manufacturer: "MEAN WELL",
      model: "DRC-100B",
    }],
  };

  assert.deepEqual(findingSourceKinds(finding), ["EQ"]);
  assert.match(findingSourceDescription(finding), /техническая документация производителя/);
  assert.doesNotMatch(findingSourceDescription(finding), /нормативное основание/);
});

test("ссылка на копию строится только из canonical job ID и EQ source ID", () => {
  const job = "00000000-0000-4000-8000-000000000001";
  const source = "EQ-" + "a".repeat(32);
  assert.equal(
    equipmentSnapshotUrl(job, source),
    "/api/v1/analyses/" + job + "/equipment-documents/" + source,
  );
  assert.equal(equipmentSnapshotUrl("../other", source), null);
  assert.equal(equipmentSnapshotUrl(job, "https://example.com"), null);
});
