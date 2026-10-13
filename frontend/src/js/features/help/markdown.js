// frontend/src/js/features/help/markdown.js

/** Читаемая Markdown-копия генерируется из того же текста, что встроенная справка. */
import { GUIDE_SECTIONS, GUIDE_UPDATED, SCENARIOS } from "./content.js";

/** Возвращает полную читаемую версию со всеми сценариями и таблицами. */
export function renderGuideMarkdown() {
  const lines = ["<!-- docs/user-guide.md -->", "", "# PDRD — руководство пользователя", "",
    "Как проверить проектную документацию и разобраться в результатах.", "",
    `Обновлено: ${GUIDE_UPDATED}. Встроенная версия: [/help.html](/help.html).`, "",
    "<!-- Сгенерировано из frontend/src/js/features/help/content.js. Изменяйте исходный текст и запускайте node scripts/export-user-guide.mjs. -->", ""];
  const escape = (text) => String(text).replaceAll("|", "\\|").replaceAll("\n", " ");
  const writeCard = (item) => {
    /** Сохраняет каждую часть карточки без сокращения пользовательского текста. */
    lines.push(`### ${item.title}`, "");
    for (const text of item.paragraphs || []) lines.push(text, "");
    (item.steps || []).forEach((text, index) => lines.push(`${index + 1}. ${text}`));
    if (item.steps) lines.push("");
    for (const [label, text] of item.facts || []) lines.push(`**${label}:** ${text}`, "");
    if (item.example) lines.push(`**Пример:** ${item.example}`, "");
    if (item.table) {
      lines.push(`| ${item.table.columns.map(escape).join(" | ")} |`, `| ${item.table.columns.map(() => "---").join(" | ")} |`);
      for (const row of item.table.rows) lines.push(`| ${row.map(escape).join(" | ")} |`);
      lines.push("");
    }
  };
  for (const section of GUIDE_SECTIONS) {
    lines.push(`## ${section.title}`, "", section.intro, "");
    for (const item of section.cards) writeCard(item);
    if (section.id === "scenarios") for (const scenario of SCENARIOS) writeCard({ title: scenario.title, facts: [
      ["Подготовьте", scenario.prepare], ["Раздел", scenario.section], ["Переключатели", scenario.toggles],
      ["Источники", scenario.sources], ["Вне проверки", scenario.outside], ["Время", scenario.duration],
    ] });
  }
  return lines.join("\n");
}
