// frontend/tests/workflow-document-context.test.js

/** Выполняет настоящие выражения и переходы D-маршрута с контролем всех HTTP-вызовов. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const workflow = JSON.parse(fs.readFileSync(new URL("../../n8n/workflows/analysis-v2-pdf.json", import.meta.url), "utf8"));
const nodes = new Map(workflow.nodes.map((node) => [node.name, node]));
const pageNumbers = [7, 10];

/** Строгий контекст не разрешает ссылаться на узлы, которые ещё не были исполнены. */
function scenario(flag, { cancelledStage = null, swappedPages = false } = {}) {
  const saved = new Map();
  const calls = [];
  const expanded = pageNumbers.map((page) => ({ page: { page_number: page, page_type: "scheme", text: `Б-012, страница ${page}` } }));
  saved.set("POST /analysis/v2/pdf", [{ json: { body: { document_id: "job", use_document_context: flag } } }]);
  saved.set("Compact PDF Metadata", [{ json: { source_sha256: "sha" } }]);
  saved.set("Document Extract PDF", [{ json: { pages: pageNumbers.map((page_number) => ({ page_number, text_words: [] })) } }]);
  saved.set("Gate Collect Understanding Stage", [{ json: { expanded_items: expanded } }]);
  const results = pageNumbers.map((page) => ({ page_number: page, result: { facts: { summary: "Помещение", document_facts: [] } } }));
  saved.set("Understand Pages Stage", [{ json: { items: results } }]);

  function context(input, index = 0) {
    return {
      $json: input[index]?.json ?? {}, $itemIndex: index,
      $input: { all: () => input, first: () => input[0] },
      $: (name) => {
        const items = saved.get(name);
        if (!items) throw new Error(`Узел не исполнен: ${name}`);
        return { first: () => items[0], all: () => items, item: items[index] };
      },
    };
  }
  function expression(value, input, index = 0) {
    return vm.runInNewContext(value.slice(3, -2), context(input, index), { timeout: 1000 });
  }
  function code(name, input) {
    return vm.runInNewContext(`(function() { ${nodes.get(name).parameters.jsCode} })()`, context(input), { timeout: 1000 });
  }
  function http(name, input) {
    const node = nodes.get(name);
    return input.map((_item, index) => {
      const body = JSON.parse(expression(node.parameters.body, input, index));
      calls.push({ name, body });
      if (name.startsWith("Progress ")) return { json: { cancelled: name === cancelledStage } };
      if (name === "Create Document Context") return { json: { context_id: "context-job" } };
      if (name === "Search Document Context") return { json: { sources: [{ page: 10, text: "Б-012" }] } };
      if (name === "Build Page Document Context") {
        const pages = swappedPages ? [...body.pages].reverse() : body.pages;
        return { json: { items: pages.map((page) => ({ page_number: page.page_number, sources: [{ source_id: `D-p${page.page_number}`, page: 10 }] })) } };
      }
      if (name === "Build Normative Queries") return { json: { queries: [body.page_facts.summary] } };
      if (name === "Check Cross-Page Consistency") {
        const items = structuredClone(body.page_checks);
        items[0].result.findings.push({ finding_id: "cross-page-0001", comment: "-37 / -35", evidence_locations: [{ page: 7 }, { page: 10 }] });
        return { json: { items } };
      }
      throw new Error(`Неизвестный HTTP-узел: ${name}`);
    });
  }
  /** Идёт по опубликованным соединениям, не подменяя код или условия ветвления. */
  function run(start, input, stop) {
    let name = start;
    while (name !== stop) {
      if (name === "Build Cancelled Result") return name;
      const node = nodes.get(name);
      let branch = 0;
      if (node.type === "n8n-nodes-base.if") {
        const condition = node.parameters.conditions.conditions[0];
        const value = expression(condition.leftValue, input);
        const match = condition.operator.operation === "notEquals"
          ? value !== condition.rightValue : value === condition.rightValue;
        branch = match ? 0 : 1;
      } else if (node.type === "n8n-nodes-base.code") input = code(name, input);
      else if (node.type === "n8n-nodes-base.httpRequest") input = http(name, input);
      else throw new Error(`Неизвестный тип: ${node.type}`);
      saved.set(name, input);
      const successors = workflow.connections[name].main[branch];
      assert.equal(successors.length, 1, name);
      name = successors[0].node;
    }
    return input;
  }

  const understood = run("Use Document Context", saved.get("Understand Pages Stage"), "Has T First Pass");
  if (understood === "Build Cancelled Result") return { calls, cancelled: true };
  saved.set("Technical Assignment First Pass", understood);
  const augmented = pageNumbers.map((page) => ({ json: { analysis_text: `Страница ${page}`, project_context_texts: [] } }));
  saved.set("Augment Project Context", augmented);
  run("Retrieve Document Context", augmented, "Search Requirements");
  saved.set("Normalize Requirement Search", pageNumbers.map(() => ({ json: {
    normative_sources: [{ source_id: "N1" }], technical_assignment_sources: [{ source_id: "T1" }], conflict_candidates: [],
  } })));
  const packages = pageNumbers.map(() => ({ json: { sources: [{ source_id: "U1" }] } }));
  const request = code("Gate Collect Norm Check Stage", packages)[0].json;
  const checks = [{ json: { items: request.items.map((page) => ({ page_number: page.page_number, result: { findings: [{ finding_id: `p${page.page_number}-f1`, comment: "N/T/U" }] } })) } }];
  saved.set("Check Norms", checks);
  const output = run("Use Cross-Page Consistency", checks, "Merge Finding Candidates");
  return { calls, request, output, cancelled: output === "Build Cancelled Result" };
}

for (const flag of [false, "false", undefined, null, "", true, "true"]) {
  test(`D=${String(flag)}: маршруты, порядок страниц и единый контракт`, () => {
    const enabled = flag === true || flag === "true";
    const result = scenario(flag);
    const counts = (name) => result.calls.filter((call) => call.name === name).length;
    for (const name of ["Progress Build Document Context", "Create Document Context", "Build Page Document Context", "Progress Cross-Page Consistency", "Check Cross-Page Consistency"]) {
      assert.equal(counts(name), Number(enabled), name);
    }
    assert.equal(counts("Search Document Context"), enabled ? pageNumbers.length : 0);
    assert.equal(counts("Build Normative Queries"), pageNumbers.length);
    assert.deepEqual(Array.from(result.request.items, (page) => page.page_number), pageNumbers);
    for (const page of result.request.items) {
      assert.equal(page.document_context_sources.length, Number(enabled));
      assert.equal(page.normative_sources[0].source_id, "N1");
      assert.equal(page.technical_assignment_sources[0].source_id, "T1");
      assert.equal(page.user_package_sources[0].source_id, "U1");
    }
    assert.deepEqual(Array.from(result.output, (item) => item.json.page_number), pageNumbers);
    assert.equal(result.output[0].json.findings.length, enabled ? 2 : 1);
    assert.equal(result.output[1].json.findings.length, 1);
  });
}

for (const stage of ["Progress Build Document Context", "Progress Cross-Page Consistency"]) {
  test(`отмена в ${stage} не запускает следующий затратный D-этап`, () => {
    const result = scenario(true, { cancelledStage: stage });
    assert.equal(result.cancelled, true);
    const forbidden = stage === "Progress Build Document Context" ? "Create Document Context" : "Check Cross-Page Consistency";
    assert.ok(!result.calls.some((call) => call.name === forbidden));
  });
}

test("неверный порядок ответа D отвергается до проверки N/T/U", () => {
  assert.throws(() => scenario(true, { swappedPages: true }), /порядок страниц/);
});
