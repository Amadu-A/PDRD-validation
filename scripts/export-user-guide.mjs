// scripts/export-user-guide.mjs

/** Обновляет читаемое руководство из единого frontend-текста; --check запрещает расхождение. */
import { readFileSync, writeFileSync } from "node:fs";
import { renderGuideMarkdown } from "../frontend/src/js/features/help/markdown.js";

const target = new URL("../docs/user-guide.md", import.meta.url);
const expected = renderGuideMarkdown();
if (process.argv.includes("--check")) {
  if (readFileSync(target, "utf8").replaceAll("\r\n", "\n") !== expected) {
    throw new Error("Руководство устарело. Выполните node scripts/export-user-guide.mjs.");
  }
} else {
  writeFileSync(target, expected, "utf8");
}
