// frontend/tests/workflow-payload.test.js

/** Проверяет объём данных Runner и сохранение растров, требований и источников. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const workflow = JSON.parse(fs.readFileSync(new URL("../../n8n/workflows/analysis-v2-pdf.json", import.meta.url), "utf8"));
const nodes = new Map(workflow.nodes.map((node) => [node.name, node]));
const documentId = "00000000-0000-4000-8000-000000000001";

/** Разрешает только указанные узлы; тяжёлый исходный ответ недоступен коду Runner. */
function harness(count, { largeImage = false, withRequirements = false } = {}) {
  const image = largeImage ? "A".repeat(2 * 1024 * 1024) : "cG5n";
  const pages = Array.from({ length: count }, (_, index) => ({
    page_number: index * 2 + 7, page_type: "scheme", text: `Б-012: лист ${index * 2 + 7}`,
    width_points: 595, height_points: 842, image_base64: largeImage ? image : `${image}${index}`,
    text_words: [{ text: "Геометрия для визуализации", x_min: 10, y_min: 20 }],
  }));
  const requirements = withRequirements ? Array.from({ length: 1000 }, (_, index) => ({
    requirement_id: `R${index}`, requirement_index: index + 1, text: "Требование к оборудованию. ".repeat(10),
  })) : [];
  const saved = new Map([
    ["POST /analysis/v2/pdf", [{ json: { body: { document_id: documentId, use_document_context: true } } }]],
    ["Document Extract PDF", [{ json: { file_name: "project.pdf", total_pages: 410,
      selected_pages: pages.map((page) => page.page_number), source_sha256: "a".repeat(64), pages,
      explanatory_note_context: { enabled: false, pages: [] } } }]],
    ["Validate Project Context", [{ json: { enabled: false, classifications: [], warnings: [] } }]],
    ["Create Project Context", [{ json: { enabled: false, context_id: documentId } }]],
    ["Technical Assignment Requirement Feed", [{ json: { enabled: withRequirements, total: requirements.length,
      technical_assignment_id: "00000000-0000-4000-8000-000000000002", analysis_document_id: documentId,
      section_id: "00000000-0000-4000-8000-000000000003", source_file: "assignment.pdf",
      source_sha256: "b".repeat(64), requirements } }]],
  ]);
  function context(input, index, runner) {
    return {
      $json: input[index]?.json ?? {}, $itemIndex: index,
      $input: { all: () => input, first: () => input[0] },
      $: (name) => {
        if (runner && name === "Document Extract PDF") throw new Error("Runner запросил исходный растр");
        const items = saved.get(name);
        if (!items) throw new Error(`Узел не исполнен: ${name}`);
        return { all: () => items, first: () => items[0], get item() { throw new Error("Запрошена вся история execution"); } };
      },
    };
  }
  function expression(value, input, index = 0) {
    return JSON.parse(vm.runInNewContext(value.slice(3, -2), context(input, index, false), { timeout: 1000 }));
  }
  function code(name, input, index = 0) {
    const result = vm.runInNewContext(`(function() { ${nodes.get(name).parameters.jsCode} })()`, context(input, index, true), { timeout: 1000 });
    const items = Array.isArray(result) ? result : [result];
    saved.set(name, items);
    return items;
  }
  const compact = expression(nodes.get("Compact PDF Metadata").parameters.jsonOutput, [{ json: { pages_count: count } }]);
  saved.set("Compact PDF Metadata", [{ json: compact }]);
  const expanded = code("Expand PDF Pages", saved.get("Technical Assignment Requirement Feed"));
  const collected = code("Gate Collect Understanding Stage", expanded);
  saved.set("Understand Pages Stage", [{ json: { items: pages.map((page) => ({ page_number: page.page_number,
    result: { facts: { summary: page.text, document_facts: [] }, metrics: {} } })) } }]);
  const understood = code("Understand Page", saved.get("Understand Pages Stage"));
  return { pages, requirements, saved, code, expression, compact, expanded, collected, understood };
}

test("200 страниц: изображения и геометрия не попадают в данные Runner", () => {
  const scenario = harness(200, { largeImage: true, withRequirements: true });
  for (const items of [scenario.compact, scenario.expanded, scenario.collected, scenario.understood]) {
    const serialized = JSON.stringify(items);
    assert.ok(!serialized.includes("image_base64"));
    assert.ok(!serialized.includes("text_words"));
    assert.ok(!serialized.includes("Требование к оборудованию"));
    assert.ok(Buffer.byteLength(serialized) < 1_000_000);
  }
  assert.equal(scenario.understood.length, 200);
  assert.deepEqual(Array.from(scenario.understood, (item) => item.json.page_number), scenario.pages.map((page) => page.page_number));
  const stage = scenario.code("Gate Collect Technical Assignment Stage", scenario.understood)[0].json;
  assert.equal(stage.requirements.length, 1000);
  assert.equal(stage.items.length, 200);
  assert.equal((JSON.stringify(stage).match(/"requirement_id"/g) ?? []).length, 1000);
  assert.ok(!JSON.stringify(stage.items).includes("image_base64"));
});

test("HTTP-этапы получают собственный растр каждой физической страницы", () => {
  const scenario = harness(3, { withRequirements: true });
  // Исходный ответ переставлен: привязка изображения должна идти по номеру листа.
  scenario.saved.get("Document Extract PDF")[0].json.pages = [...scenario.pages].reverse();
  const technical = scenario.code("Gate Collect Technical Assignment Stage", scenario.understood);
  const inputs = new Map([
    ["Understand Pages Stage", scenario.collected],
    ["Check Technical Assignment", technical],
    ["Build Page Document Context", [{ json: { pages: scenario.pages.map((page) => ({ page_number: page.page_number, extracted_text: page.text, page_facts: {} })), semantic: [] } }]],
    ["Check Norms", [{ json: { document_id: documentId, items: scenario.pages.map((page) => ({
      page_number: page.page_number, extracted_text: page.text, page_facts: { summary: page.text },
      normative_sources: [{ source_id: "N1" }], technical_assignment_sources: [{ source_id: "T1" }],
      conflict_candidates: [], user_package_sources: [{ source_id: "U1" }], document_context_sources: [{ source_id: "D1" }],
    })) } }]],
  ]);
  for (const [name, input] of inputs) {
    const body = scenario.expression(nodes.get(name).parameters.body, input);
    if (name === "Build Page Document Context") {
      assert.equal(body.enabled, true);
      body.pages.forEach((page, index) => {
        assert.equal(page.page_number, scenario.pages[index].page_number);
        assert.deepEqual(page.text_words, scenario.pages[index].text_words);
        assert.equal(page.image_base64, undefined);
      });
      continue;
    }
    assert.equal(body.document_id, documentId);
    assert.equal(body.items.length, scenario.pages.length);
    body.items.forEach((item, index) => {
      assert.equal(item.page_number, scenario.pages[index].page_number);
      assert.equal(item.image_base64, scenario.pages[index].image_base64);
      assert.equal(item.extracted_text, scenario.pages[index].text);
    });
    if (name === "Check Technical Assignment") assert.equal(body.requirements.length, 1000);
    if (name === "Check Norms") {
      for (const key of ["normative_sources", "technical_assignment_sources", "user_package_sources", "document_context_sources"]) {
        assert.equal(body.items[0][key].length, 1, key);
      }
    }
  }
});

test("T-first проверяет полный список требований без копии в каждой странице", () => {
  const scenario = harness(2, { withRequirements: true });
  const response = [{ json: { items: scenario.pages.map((page) => ({ page_number: page.page_number,
    result: { decisions: scenario.requirements.map((requirement) => ({ requirement_id: requirement.requirement_id })),
      findings: [{ finding_id: `t-${page.page_number}` }], metrics: [] } })) } }];
  const prepared = scenario.code("Technical Assignment First Pass", response);
  assert.equal(prepared.length, 2);
  for (const item of prepared) {
    assert.equal(item.json.requirements_count, 1000);
    assert.equal(item.json.decisions.length, 1000);
    assert.equal(item.json.findings.length, 1);
    assert.equal(item.json.expanded.technical_assignment.requirements, undefined);
  }
  response[0].json.items[1].result.decisions.pop();
  assert.throws(() => scenario.code("Technical Assignment First Pass", response), /ordered atomic requirement feed/);
});

/** Выполняет реальный код подготовки запросов и привязки N/E-источников. */
function enrichmentScenario({ badOrder = false, missingNorm = false, wrongNorm = false, missingExperience = false } = {}) {
  const scenario = harness(3);
  const prepared = scenario.pages.map((page, page_index) => scenario.code("Prepare Finding Normative Queries", [{ json: {
    page_index, page_number: page.page_number, findings: [{ finding_id: `p${page.page_number}-f1`, experience_query: `Запрос ${page.page_number}` }],
  } }])[0]);
  if (badOrder) [prepared[0], prepared[1]] = [prepared[1], prepared[0]];
  scenario.saved.set("Prepare Finding Normative Queries", prepared);
  const inputs = prepared.map((item) => ({ json: { results: missingNorm ? [] : item.json.normative_queries.map((query) => ({
    query: wrongNorm ? "Чужой запрос" : query, sources: [{ source_id: "retrieval-id", page: item.json.page_number }],
  })) } }));
  const enriched = inputs.map((_item, index) => scenario.code("Prepare Experience Queries", inputs, index)[0]);
  scenario.saved.set("Prepare Experience Queries", enriched);
  const experience = enriched.map((item) => ({ json: { results: missingExperience ? [] : item.json.experience_query_items.map(({ query }) => ({
    query, sources: [{ example_id: `e-${item.json.page_number}` }],
  })) } }));
  const mapped = experience.map((_item, index) => scenario.code("Build Experience Map", experience, index)[0]);
  return { scenario, mapped };
}

test("N/E-поиск и финализация сохраняют все замечания и собственные источники страницы", () => {
  const { scenario, mapped } = enrichmentScenario();
  const prepared = scenario.code("Technical Assignment First Pass", scenario.understood);
  scenario.saved.set("Technical Assignment First Pass", prepared);
  const finalization = scenario.code("Gate Collect Finalization Stage", mapped)[0].json;
  assert.equal(finalization.items.length, 3);
  finalization.items.forEach((item, index) => {
    const page = scenario.pages[index].page_number;
    const findingId = `p${page}-f1`;
    assert.equal(item.page_number, page);
    assert.equal(item.request.findings[0].finding_id, findingId);
    assert.equal(item.request.normative_candidates_by_finding[findingId][0].page, page);
    assert.equal(item.request.normative_candidates_by_finding[findingId][0].source_id, "NQ1_1");
    assert.equal(item.request.experience_by_finding[findingId][0].example_id, `e-${page}`);
  });
  [mapped[0], mapped[1]] = [mapped[1], mapped[0]];
  assert.throws(() => scenario.code("Gate Collect Finalization Stage", mapped), /порядок страниц/);
});

for (const [options, message] of [
  [{ badOrder: true }, /порядок страниц/],
  [{ missingNorm: true }, /неполный набор/],
  [{ wrongNorm: true }, /несогласованный результат/],
  [{ missingExperience: true }, /неполный набор/],
]) {
  test(`поиск отвергает ошибочную привязку: ${JSON.stringify(options)}`, () => {
    assert.throws(() => enrichmentScenario(options), message);
  });
}
