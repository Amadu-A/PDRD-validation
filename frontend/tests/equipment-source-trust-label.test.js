// frontend/tests/equipment-source-trust-label.test.js

/** Пометка разрешения относится к моменту загрузки и не заменяет применимость паспорта. */
import assert from "node:assert/strict";
import test from "node:test";
import { equipmentSourceTrustLabel } from "../src/js/features/analysis/equipment-report.js";

test("разрешённый сайт не превращает документ в автоматически применимый", () => {
  const label = equipmentSourceTrustLabel({ trust_status: "trusted" });
  assert.match(label, /при загрузке/);
  assert.doesNotMatch(label, /применим|соответствует/);
});
test("разрешение неподтверждённого сайта относится только к своему заданию", () => {
  assert.match(equipmentSourceTrustLabel({ trust_status: "unverified_user_override" }), /только для этого задания/);
});
test("неизвестное разрешение не объявляется подтверждённым или разрешённым пользователем", () => {
  assert.match(equipmentSourceTrustLabel({}), /не установлено/);
});
