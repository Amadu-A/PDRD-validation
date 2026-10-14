// frontend/tests/analysis-submit-guard.test.js

/** Проверяет независимые блокировки запуска и доступную подсказку причины. */
import assert from "node:assert/strict";
import test from "node:test";
import { setAnalysisSubmitBlock } from "../src/js/features/analysis/submit-guard.js";

test("снятие блокировки ТЗ и закрытие modal не разрешают запуск без раздела", () => {
  const wrapper = {};
  const button = { parentElement: wrapper };
  setAnalysisSubmitBlock(button, "section", "Выберите раздел, чтобы запустить анализ.");
  setAnalysisSubmitBlock(button, "technical_assignment", "ТЗ индексируется.");
  setAnalysisSubmitBlock(button, "analysis", "Анализ выполняется.");
  setAnalysisSubmitBlock(button, "technical_assignment");
  setAnalysisSubmitBlock(button, "analysis");
  assert.equal(button.disabled, true);
  assert.equal(button.title, "Выберите раздел, чтобы запустить анализ.");
  assert.equal(wrapper.title, button.title);
  setAnalysisSubmitBlock(button, "section");
  assert.equal(button.disabled, false);
  assert.equal(wrapper.title, "");
});

test("смена раздела во время анализа сохраняет блокировку активной работы", () => {
  const button = {};
  setAnalysisSubmitBlock(button, "analysis", "Анализ выполняется.");
  setAnalysisSubmitBlock(button, "section", "Выберите раздел.");
  setAnalysisSubmitBlock(button, "section");
  assert.equal(button.disabled, true);
  assert.equal(button.title, "Анализ выполняется.");
});
