# ops/apply-large-document-resilience.py

"""Применяет Stage 8.4 large-document resilience."""

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

PDF_WORKFLOW = ROOT / "n8n" / "workflows" / "analysis-v2-pdf.json"

ALL_WORKFLOWS = (
    PDF_WORKFLOW,
    ROOT / "n8n" / "workflows" / "analysis-v2-cad.json",
    ROOT / "n8n" / "workflows" / "analysis-v2-pdf-cad.json",
)

ENV_PATH = ROOT / ".env.example"

GPU_STAGE_TIMEOUT_MS = 3_300_000

WORKFLOW_TIMEOUT_SECONDS = 3450


def _load_workflow(
    path: Path,
) -> dict[str, Any]:
    """Читает workflow JSON."""
    payload = json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            f"{path}: workflow root must be object.",
        )

    return payload


def _nodes(
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    """Возвращает mutable nodes."""
    raw_nodes = workflow.get(
        "nodes",
    )

    if not isinstance(
        raw_nodes,
        list,
    ):
        raise ValueError(
            "Workflow nodes must be list.",
        )

    return raw_nodes


def _node(
    workflow: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    """Находит node по имени."""
    for node in _nodes(
        workflow,
    ):
        if (
            isinstance(
                node,
                dict,
            )
            and node.get(
                "name",
            )
            == name
        ):
            return node

    raise ValueError(
        f"Missing workflow node: {name}",
    )


def _node_exists(
    workflow: dict[str, Any],
    name: str,
) -> bool:
    """Проверяет наличие node."""
    return any(
        isinstance(
            node,
            dict,
        )
        and node.get(
            "name",
        )
        == name
        for node in _nodes(
            workflow,
        )
    )


def _connection(
    target: str,
    *,
    input_index: int = 0,
) -> dict[str, Any]:
    """Стандартный n8n main connection."""
    return {
        "node": target,
        "type": "main",
        "index": input_index,
    }


def _set_main(
    workflow: dict[str, Any],
    source: str,
    targets: list[
        tuple[
            str,
            int,
        ]
    ],
) -> None:
    """Заменяет первый main output."""
    connections = workflow.get(
        "connections",
    )

    if not isinstance(
        connections,
        dict,
    ):
        raise ValueError(
            "Workflow connections must be object.",
        )

    connections[source] = {
        "main": [
            [
                _connection(
                    target,
                    input_index=input_index,
                )
                for (
                    target,
                    input_index,
                ) in targets
            ]
        ]
    }


def _rename_node(
    workflow: dict[str, Any],
    *,
    old_name: str,
    new_name: str,
) -> None:
    """Переименовывает node и topology references."""
    if _node_exists(
        workflow,
        new_name,
    ):
        return

    node = _node(
        workflow,
        old_name,
    )

    node["name"] = new_name

    connections = workflow.get(
        "connections",
    )

    if not isinstance(
        connections,
        dict,
    ):
        raise ValueError(
            "Workflow connections must be object.",
        )

    if old_name in connections:
        connections[new_name] = connections.pop(
            old_name,
        )

    for source in connections.values():
        if not isinstance(
            source,
            dict,
        ):
            continue

        main = source.get(
            "main",
            [],
        )

        if not isinstance(
            main,
            list,
        ):
            continue

        for output in main:
            if not isinstance(
                output,
                list,
            ):
                continue

            for item in output:
                if (
                    isinstance(
                        item,
                        dict,
                    )
                    and item.get(
                        "node",
                    )
                    == old_name
                ):
                    item["node"] = new_name


def _ensure_code_node(
    workflow: dict[str, Any],
    *,
    name: str,
    node_id: str,
    js_code: str,
    position: list[int],
) -> dict[str, Any]:
    """Создаёт/нормализует Code node."""
    if _node_exists(
        workflow,
        name,
    ):
        node = _node(
            workflow,
            name,
        )

    else:
        node = {
            "id": node_id,
            "name": name,
        }

        _nodes(
            workflow,
        ).append(
            node,
        )

    node["parameters"] = {
        "mode": "runOnceForAllItems",
        "jsCode": js_code.strip(),
    }

    node["type"] = "n8n-nodes-base.code"
    node["typeVersion"] = 2
    node["position"] = position

    node.pop(
        "retryOnFail",
        None,
    )

    node.pop(
        "maxTries",
        None,
    )

    node.pop(
        "waitBetweenTries",
        None,
    )

    return node


def _normalize_http_stage(
    *,
    node: dict[str, Any],
    url: str,
    body: str,
) -> None:
    """Переводит HTTP node на long-running stage endpoint."""
    node["parameters"] = {
        "method": "POST",
        "url": url,
        "sendBody": True,
        "contentType": "raw",
        "rawContentType": "application/json",
        "body": body,
        "options": {
            "timeout": GPU_STAGE_TIMEOUT_MS,
        },
    }

    node["type"] = "n8n-nodes-base.httpRequest"
    node["typeVersion"] = 4.2
    node["retryOnFail"] = False

    node.pop(
        "maxTries",
        None,
    )

    node.pop(
        "waitBetweenTries",
        None,
    )


def _apply_pdf_workflow(
    workflow: dict[str, Any],
) -> None:
    """Переводит PDF-only GPU fan-out на stage-scoped batching."""
    _rename_node(
        workflow,
        old_name="Understand Page",
        new_name="Understand Pages Stage",
    )

    collect_understanding_code = """
const expandedItems = $input.all().map((item) => item.json);

if (!expandedItems.length) {
  throw new Error('Understanding stage не получил PDF-страницы.');
}

return [{
  json: {
    document_id:
      $('POST /analysis/v2/pdf').first().json.body.document_id,
    expanded_items: expandedItems,
    items: expandedItems.map((item) => ({
      page_number: item.page.page_number,
      heuristic_page_type: item.page.page_type,
      extracted_text: item.page.text,
      image_base64: item.page.image_base64,
    })),
  },
}];
"""

    expand_understanding_code = """
const source = $('Gate Collect Understanding Stage').first().json;
const expandedItems = Array.isArray(source.expanded_items)
  ? source.expanded_items
  : [];
const results = Array.isArray($json.items)
  ? $json.items
  : [];

if (results.length !== expandedItems.length) {
  throw new Error(
    `Understanding stage потерял страницы: expected=${expandedItems.length}, actual=${results.length}.`,
  );
}

return results.map((entry, index) => {
  const expanded = expandedItems[index];
  const expectedPage = Number(expanded?.page?.page_number ?? 0);
  const actualPage = Number(entry?.page_number ?? 0);

  if (!expectedPage || actualPage !== expectedPage) {
    throw new Error(
      `Understanding stage нарушил порядок страниц: expected=${expectedPage}, actual=${actualPage}.`,
    );
  }

  return {
    json: {
      page_number: actualPage,
      expanded,
      facts: entry?.result?.facts ?? {},
      understanding_metrics:
        entry?.result?.metrics ?? {},
    },
  };
});
"""

    collect_t_code = """
const pageItems = $input.all().map((item) => item.json);

if (!pageItems.length) {
  throw new Error('T-first stage не получил страницы.');
}

const technicalAssignment =
  pageItems[0]?.expanded?.technical_assignment ?? {};

const requirements = Array.isArray(
  technicalAssignment.requirements,
)
  ? technicalAssignment.requirements
  : [];

if (!requirements.length) {
  throw new Error(
    'T-first включён, но atomic requirement feed пуст.',
  );
}

return [{
  json: {
    document_id:
      $('POST /analysis/v2/pdf').first().json.body.document_id,
    technical_assignment_id:
      technicalAssignment.technical_assignment_id,
    analysis_document_id:
      technicalAssignment.analysis_document_id,
    section_id:
      technicalAssignment.section_id,
    source_file:
      technicalAssignment.source_file,
    source_sha256:
      technicalAssignment.source_sha256,
    requirements,
    items: pageItems.map((item) => ({
      page_number:
        item.expanded.page.page_number,
      extracted_text:
        item.expanded.page.text,
      page_facts:
        item.facts ?? {},
      image_base64:
        item.expanded.page.image_base64,
    })),
  },
}];
"""

    t_first_code = """
const understoodPages = $('Understand Page')
  .all()
  .map((item) => item.json);

if (!understoodPages.length) {
  throw new Error(
    'T-first normalization не получила understood pages.',
  );
}

const enabled = Boolean(
  understoodPages[0]?.expanded
    ?.technical_assignment?.enabled,
);

if (!enabled) {
  return understoodPages.map((page) => ({
    json: {
      ...page,
      enabled: false,
      requirements_count: 0,
      summary: '',
      decisions: [],
      findings: [],
      metrics: [],
    },
  }));
}

const batchItems = Array.isArray(
  $input.first().json.items,
)
  ? $input.first().json.items
  : [];

if (batchItems.length !== understoodPages.length) {
  throw new Error(
    `T-first stage потеряла страницы: expected=${understoodPages.length}, actual=${batchItems.length}.`,
  );
}

const byPage = new Map(
  batchItems.map((item) => [
    Number(item.page_number ?? 0),
    item.result ?? {},
  ]),
);

return understoodPages.map((page) => {
  const pageNumber = Number(
    page.expanded?.page?.page_number ?? 0,
  );

  const response = byPage.get(pageNumber);

  if (!response) {
    throw new Error(
      `T-first stage не вернула страницу ${pageNumber}.`,
    );
  }

  const decisions = Array.isArray(response.decisions)
    ? response.decisions
    : [];

  const findings = Array.isArray(response.findings)
    ? response.findings
    : [];

  const metrics = Array.isArray(response.metrics)
    ? response.metrics
    : [];

  const requirements = Array.isArray(
    page.expanded?.technical_assignment?.requirements,
  )
    ? page.expanded.technical_assignment.requirements
    : [];

  const expectedIds = requirements.map(
    (requirement) =>
      String(requirement.requirement_id ?? ''),
  );

  const actualIds = decisions.map(
    (decision) =>
      String(decision.requirement_id ?? ''),
  );

  if (
    actualIds.length !== expectedIds.length
    || actualIds.some((id) => !id)
    || new Set(actualIds).size !== actualIds.length
    || expectedIds.some(
      (id, index) => id !== actualIds[index],
    )
  ) {
    throw new Error(
      `T-first decisions страницы ${pageNumber} не соответствуют полному ordered atomic requirement feed.`,
    );
  }

  return {
    json: {
      ...page,
      enabled: true,
      requirements_count: expectedIds.length,
      summary: String(response.summary ?? ''),
      decisions,
      findings,
      metrics,
    },
  };
});
"""

    collect_norm_code = """
const preparedPages = $('Technical Assignment First Pass')
  .all()
  .map((item) => item.json);

const augmentations = $('Augment Project Context')
  .all()
  .map((item) => item.json);

const requirements = $('Normalize Requirement Search')
  .all()
  .map((item) => item.json);

const userPackages = $input.all()
  .map((item) => item.json);

const expected = preparedPages.length;

if (
  augmentations.length !== expected
  || requirements.length !== expected
  || userPackages.length !== expected
) {
  throw new Error(
    'Normative stage потеряла page-local retrieval context.',
  );
}

return [{
  json: {
    document_id:
      $('POST /analysis/v2/pdf').first().json.body.document_id,
    items: preparedPages.map((page, index) => ({
      page_number:
        page.expanded.page.page_number,
      extracted_text:
        augmentations[index].analysis_text ?? '',
      page_facts:
        page.facts ?? {},
      normative_sources:
        requirements[index].normative_sources ?? [],
      technical_assignment_sources:
        requirements[index]
          .technical_assignment_sources ?? [],
      conflict_candidates:
        requirements[index].conflict_candidates ?? [],
      user_package_sources:
        userPackages[index].sources ?? [],
      image_base64:
        page.expanded.page.image_base64,
      normative_system_prompt:
        $('POST /analysis/v2/pdf')
          .first()
          .json.body?.normative_system_prompt
          ?? null,
    })),
  },
}];
"""

    expand_norm_code = """
const preparedPages = $('Technical Assignment First Pass')
  .all()
  .map((item) => item.json);

const results = Array.isArray($json.items)
  ? $json.items
  : [];

if (results.length !== preparedPages.length) {
  throw new Error(
    `Normative stage потеряла страницы: expected=${preparedPages.length}, actual=${results.length}.`,
  );
}

return results.map((entry, index) => {
  const expectedPage = Number(
    preparedPages[index]?.expanded
      ?.page?.page_number ?? 0,
  );

  const actualPage = Number(
    entry?.page_number ?? 0,
  );

  if (
    !expectedPage
    || actualPage !== expectedPage
  ) {
    throw new Error(
      `Normative stage нарушил порядок страниц: expected=${expectedPage}, actual=${actualPage}.`,
    );
  }

  return {
    json: {
      page_index: index,
      page_number: actualPage,
      ...(
        entry?.result ?? {}
      ),
    },
  };
});
"""

    merge_code = """
const normativeCheck = $json ?? {};

const normativeFindings = Array.isArray(
  normativeCheck.findings,
)
  ? normativeCheck.findings
  : [];

const pageIndex = Number(
  normativeCheck.page_index ?? $itemIndex,
);

const technicalAssignment =
  $('Technical Assignment First Pass')
    .all()[pageIndex]?.json ?? {};

const technicalAssignmentFindings =
  Array.isArray(technicalAssignment.findings)
    ? technicalAssignment.findings
    : [];

const findings = [
  ...normativeFindings,
  ...technicalAssignmentFindings,
];

const findingIds = findings.map(
  (finding) =>
    String(finding.finding_id ?? ''),
);

if (
  findingIds.some((findingId) => !findingId)
  || new Set(findingIds).size
    !== findingIds.length
) {
  throw new Error(
    'Finding candidate merge обнаружил пустой или повторяющийся finding_id.',
  );
}

return {
  json: {
    ...normativeCheck,
    findings,
    technical_assignment_first_pass: {
      enabled:
        Boolean(technicalAssignment.enabled),
      requirements_count:
        Number(
          technicalAssignment.requirements_count
          ?? 0,
        ),
      decisions_count:
        Array.isArray(technicalAssignment.decisions)
          ? technicalAssignment.decisions.length
          : 0,
      findings_count:
        technicalAssignmentFindings.length,
      summary:
        String(technicalAssignment.summary ?? ''),
      metrics:
        Array.isArray(technicalAssignment.metrics)
          ? technicalAssignment.metrics
          : [],
    },
  },
};
"""

    collect_finalization_code = """
const experienceItems = $input.all()
  .map((item) => item.json);

const preparedPages =
  $('Technical Assignment First Pass')
    .all()
    .map((item) => item.json);

if (experienceItems.length !== preparedPages.length) {
  throw new Error(
    'Finalization stage потеряла page-local context.',
  );
}

return [{
  json: {
    document_id:
      $('POST /analysis/v2/pdf').first().json.body.document_id,
    items: experienceItems.map((item, index) => ({
      page_number:
        preparedPages[index]
          .expanded.page.page_number,
      request: {
        findings:
          item.findings ?? [],
        experience_by_finding:
          item.experience_by_finding ?? {},
        normative_candidates: [],
        normative_candidates_by_finding:
          item.normative_candidates_by_finding ?? {},
      },
    })),
  },
}];
"""

    expand_finalization_code = """
const preparedPages =
  $('Technical Assignment First Pass')
    .all()
    .map((item) => item.json);

const results = Array.isArray($json.items)
  ? $json.items
  : [];

if (results.length !== preparedPages.length) {
  throw new Error(
    `Finalization stage потеряла страницы: expected=${preparedPages.length}, actual=${results.length}.`,
  );
}

return results.map((entry, index) => {
  const expectedPage = Number(
    preparedPages[index]?.expanded
      ?.page?.page_number ?? 0,
  );

  const actualPage = Number(
    entry?.page_number ?? 0,
  );

  if (
    !expectedPage
    || actualPage !== expectedPage
  ) {
    throw new Error(
      `Finalization stage нарушил порядок страниц: expected=${expectedPage}, actual=${actualPage}.`,
    );
  }

  return {
    json: {
      page_index: index,
      page_number: actualPage,
      finalization:
        entry?.result ?? {},
    },
  };
});
"""

    build_page_result_code = """
const index = Number(
  $json.page_index ?? $itemIndex,
);

const prepared =
  $('Technical Assignment First Pass')
    .all()[index]?.json ?? {};

const expanded =
  prepared.expanded ?? {};

const understanding = {
  facts:
    prepared.facts ?? {},
  metrics:
    prepared.understanding_metrics ?? {},
};

const queries =
  $('Build Normative Queries')
    .all()[index]?.json ?? {};

const requirements =
  $('Normalize Requirement Search')
    .all()[index]?.json ?? {};

const userPackages =
  $('Search User Packages')
    .all()[index]?.json ?? {};

const check =
  $('Gate Expand Norm Check Stage')
    .all()[index]?.json ?? {};

const merged =
  $('Merge Finding Candidates')
    .all()[index]?.json ?? {};

const tFirst = prepared;

const enrichment =
  $('Prepare Experience Queries')
    .all()[index]?.json ?? {};

const augmentation =
  $('Augment Project Context')
    .all()[index]?.json ?? {};

const findingQuery =
  $('Prepare Finding Normative Queries')
    .all()[index]?.json ?? {};

const finalization =
  $json.finalization ?? {};

const findings = (
  finalization.findings ?? []
).map((finding) => ({
  ...finding,
  project_context_sources:
    augmentation.sources ?? [],
}));

const candidatesByFinding =
  enrichment.normative_candidates_by_finding
  && typeof enrichment.normative_candidates_by_finding
    === 'object'
    ? enrichment.normative_candidates_by_finding
    : {};

const candidateGroups = Object.values(
  candidatesByFinding,
).filter(
  (value) => Array.isArray(value),
);

const candidatesCount = candidateGroups
  .reduce(
    (total, sources) =>
      total + sources.length,
    0,
  );

const findingsWithCandidates =
  candidateGroups
    .filter(
      (sources) => sources.length > 0,
    )
    .length;

const tDecisions = Array.isArray(
  tFirst.decisions,
)
  ? tFirst.decisions
  : [];

return {
  json: {
    document:
      expanded.document,
    project_context:
      expanded.project_context,
    page_number:
      expanded.page.page_number,
    heuristic_page_type:
      expanded.page.page_type,
    width_points:
      expanded.page.width_points,
    height_points:
      expanded.page.height_points,
    extracted_text:
      expanded.page.text,
    facts:
      understanding.facts ?? {},
    normative_queries:
      queries.queries ?? [],
    normative_sources:
      requirements.normative_sources ?? [],
    technical_assignment_sources:
      requirements.technical_assignment_sources ?? [],
    conflict_candidates:
      requirements.conflict_candidates ?? [],
    requirement_diagnostics:
      requirements.diagnostics ?? [],
    reference_resolutions:
      requirements.reference_resolutions ?? [],
    user_package_sources:
      userPackages.sources ?? [],
    technical_assignment_first_pass: {
      enabled:
        Boolean(tFirst.enabled),
      requirements_count:
        Number(tFirst.requirements_count ?? 0),
      decisions_count:
        tDecisions.length,
      satisfied_count:
        tDecisions.filter(
          (decision) =>
            decision.status === 'satisfied',
        ).length,
      not_applicable_count:
        tDecisions.filter(
          (decision) =>
            decision.status === 'not_applicable',
        ).length,
      violated_count:
        tDecisions.filter(
          (decision) =>
            decision.status === 'violated',
        ).length,
      needs_review_count:
        tDecisions.filter(
          (decision) =>
            decision.status
              === 'insufficient_evidence',
        ).length,
      findings_count:
        Array.isArray(tFirst.findings)
          ? tFirst.findings.length
          : 0,
      summary:
        String(tFirst.summary ?? ''),
      metrics:
        Array.isArray(tFirst.metrics)
          ? tFirst.metrics
          : [],
    },
    finding_candidates: {
      normative_check_count:
        Array.isArray(check.findings)
          ? check.findings.length
          : 0,
      merged_count:
        Array.isArray(merged.findings)
          ? merged.findings.length
          : 0,
    },
    normative_enrichment: {
      queries_count:
        findingQuery.normative_queries?.length
        ?? 0,
      findings_with_candidates:
        findingsWithCandidates,
      candidates_count:
        candidatesCount,
    },
    normative_check: {
      summary:
        check.summary ?? '',
      findings:
        check.findings ?? [],
      metrics:
        check.metrics ?? {},
    },
    findings,
  },
};
"""

    _ensure_code_node(
        workflow,
        name="Gate Collect Understanding Stage",
        node_id=("8d400000-0000-4000-8000-000000000001"),
        js_code=collect_understanding_code,
        position=[
            -760,
            980,
        ],
    )

    understanding_stage = _node(
        workflow,
        "Understand Pages Stage",
    )

    _normalize_http_stage(
        node=understanding_stage,
        url=("http://pdrd-analysis-service:8501/internal/v1/stages/understand-pages"),
        body=(
            "={{ JSON.stringify({ "
            "document_id: $json.document_id, "
            "items: $json.items "
            "}) }}"
        ),
    )

    _ensure_code_node(
        workflow,
        name="Understand Page",
        node_id=("8d400000-0000-4000-8000-000000000002"),
        js_code=expand_understanding_code,
        position=[
            -500,
            980,
        ],
    )

    has_t = _node(
        workflow,
        "Has T First Pass",
    )

    has_t["parameters"]["conditions"]["conditions"][0]["leftValue"] = (
        "={{ Boolean($json.expanded?.technical_assignment?.enabled) }}"
    )

    _ensure_code_node(
        workflow,
        name=("Gate Collect Technical Assignment Stage"),
        node_id=("8d400000-0000-4000-8000-000000000003"),
        js_code=collect_t_code,
        position=[
            -320,
            520,
        ],
    )

    check_t = _node(
        workflow,
        "Check Technical Assignment",
    )

    _normalize_http_stage(
        node=check_t,
        url=(
            "http://pdrd-analysis-service:8501"
            "/internal/v1/stages/"
            "check-technical-assignment"
        ),
        body="={{ JSON.stringify($json) }}",
    )

    t_first = _node(
        workflow,
        "Technical Assignment First Pass",
    )

    t_first["parameters"] = {
        "mode": "runOnceForAllItems",
        "jsCode": t_first_code.strip(),
    }

    t_first["type"] = "n8n-nodes-base.code"
    t_first["typeVersion"] = 2

    build_context_query = _node(
        workflow,
        "Build Project Context Query",
    )

    build_context_query["parameters"]["body"] = (
        "={{ JSON.stringify({ "
        "page_facts: $json.facts, "
        "extracted_text: $json.expanded.page.text "
        "}) }}"
    )

    search_context = _node(
        workflow,
        "Search Project Context",
    )

    search_context["parameters"]["body"] = (
        "={{ JSON.stringify({ "
        "context_id: "
        "$('Technical Assignment First Pass')"
        ".all()[$itemIndex].json.expanded"
        ".project_context.context_id, "
        "enabled: "
        "$('Technical Assignment First Pass')"
        ".all()[$itemIndex].json.expanded"
        ".project_context.enabled, "
        "query: $json.query ?? '' "
        "}) }}"
    )

    augment_context = _node(
        workflow,
        "Augment Project Context",
    )

    augment_context["parameters"]["body"] = (
        "={{ JSON.stringify({ "
        "extracted_text: "
        "$('Technical Assignment First Pass')"
        ".all()[$itemIndex].json.expanded.page.text, "
        "sources: $json.sources ?? [] "
        "}) }}"
    )

    build_queries = _node(
        workflow,
        "Build Normative Queries",
    )

    build_queries["parameters"]["body"] = (
        "={{ JSON.stringify({ "
        "page_facts: "
        "$('Technical Assignment First Pass')"
        ".all()[$itemIndex].json.facts, "
        "extracted_text: "
        "$('Technical Assignment First Pass')"
        ".all()[$itemIndex].json.expanded.page.text, "
        "project_context_texts: "
        "$json.project_context_texts ?? [] "
        "}) }}"
    )

    _ensure_code_node(
        workflow,
        name="Gate Collect Norm Check Stage",
        node_id=("8d400000-0000-4000-8000-000000000004"),
        js_code=collect_norm_code,
        position=[
            1680,
            980,
        ],
    )

    check_norms = _node(
        workflow,
        "Check Norms",
    )

    _normalize_http_stage(
        node=check_norms,
        url=("http://pdrd-analysis-service:8501/internal/v1/stages/check-norms"),
        body=(
            "={{ JSON.stringify({ "
            "document_id: $json.document_id, "
            "items: $json.items.map((item) => ({ "
            "page_number: item.page_number, "
            "extracted_text: item.extracted_text, "
            "page_facts: item.page_facts, "
            "normative_sources: item.normative_sources, "
            "technical_assignment_sources: "
            "item.technical_assignment_sources, "
            "conflict_candidates: "
            "item.conflict_candidates, "
            "user_package_sources: "
            "item.user_package_sources, "
            "image_base64: item.image_base64, "
            "normative_system_prompt: "
            "item.normative_system_prompt "
            "})) "
            "}) }}"
        ),
    )

    _ensure_code_node(
        workflow,
        name="Gate Expand Norm Check Stage",
        node_id=("8d400000-0000-4000-8000-000000000005"),
        js_code=expand_norm_code,
        position=[
            1900,
            980,
        ],
    )

    merge = _node(
        workflow,
        "Merge Finding Candidates",
    )

    merge["parameters"] = {
        "mode": "runOnceForEachItem",
        "jsCode": merge_code.strip(),
    }

    _ensure_code_node(
        workflow,
        name="Gate Collect Finalization Stage",
        node_id=("8d400000-0000-4000-8000-000000000006"),
        js_code=collect_finalization_code,
        position=[
            3220,
            980,
        ],
    )

    finalize = _node(
        workflow,
        "Finalize Findings",
    )

    _normalize_http_stage(
        node=finalize,
        url=("http://pdrd-analysis-service:8501/internal/v1/stages/finalize"),
        body=(
            "={{ JSON.stringify({ "
            "document_id: $json.document_id, "
            "items: $json.items.map((item) => ({ "
            "page_number: item.page_number, "
            "request: { "
            "findings: item.request.findings, "
            "experience_by_finding: "
            "item.request.experience_by_finding, "
            "normative_candidates: "
            "item.request.normative_candidates, "
            "normative_candidates_by_finding: "
            "item.request.normative_candidates_by_finding "
            "} "
            "})) "
            "}) }}"
        ),
    )

    _ensure_code_node(
        workflow,
        name="Gate Expand Finalization Stage",
        node_id=("8d400000-0000-4000-8000-000000000007"),
        js_code=expand_finalization_code,
        position=[
            3460,
            980,
        ],
    )

    build_page_result = _node(
        workflow,
        "Build Page Result",
    )

    build_page_result["parameters"] = {
        "mode": "runOnceForEachItem",
        "jsCode": build_page_result_code.strip(),
    }

    _set_main(
        workflow,
        "Gate Understand Sheet",
        [
            (
                "Gate Collect Understanding Stage",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Gate Collect Understanding Stage",
        [
            (
                "Understand Pages Stage",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Understand Pages Stage",
        [
            (
                "Understand Page",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Understand Page",
        [
            (
                "Has T First Pass",
                0,
            ),
        ],
    )

    connections = workflow["connections"]

    connections["Has T First Pass"] = {
        "main": [
            [_connection("Gate Collect Technical Assignment Stage")],
            [_connection("Technical Assignment First Pass")],
        ]
    }

    _set_main(
        workflow,
        ("Gate Collect Technical Assignment Stage"),
        [
            (
                "Check Technical Assignment",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Check Technical Assignment",
        [
            (
                "Technical Assignment First Pass",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Gate Check Requirements",
        [
            (
                "Gate Collect Norm Check Stage",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Gate Collect Norm Check Stage",
        [
            (
                "Check Norms",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Check Norms",
        [
            (
                "Gate Expand Norm Check Stage",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Gate Expand Norm Check Stage",
        [
            (
                "Merge Finding Candidates",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Gate Finalize Findings",
        [
            (
                "Gate Collect Finalization Stage",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Gate Collect Finalization Stage",
        [
            (
                "Finalize Findings",
                0,
            ),
        ],
    )

    # Finalize Findings сохраняет существующий
    # Progress Build Result + Gate Build Result.
    _set_main(
        workflow,
        "Gate Build Result",
        [
            (
                "Gate Expand Finalization Stage",
                0,
            ),
        ],
    )

    _set_main(
        workflow,
        "Gate Expand Finalization Stage",
        [
            (
                "Build Page Result",
                0,
            ),
        ],
    )


def _set_env_value(
    source: str,
    *,
    key: str,
    value: str,
) -> str:
    """Заменяет существующее ENV значение."""
    pattern = re.compile(
        rf"(?m)^{re.escape(key)}=.*$",
    )

    if (
        pattern.search(
            source,
        )
        is None
    ):
        raise ValueError(
            f"Missing env key: {key}",
        )

    return pattern.sub(
        f"{key}={value}",
        source,
    )


def _ensure_env_after(
    source: str,
    *,
    anchor: str,
    line: str,
) -> str:
    """Добавляет новую ENV строку один раз."""
    key = line.split(
        "=",
        maxsplit=1,
    )[0]

    if re.search(
        rf"(?m)^{re.escape(key)}=",
        source,
    ):
        return source

    marker = f"{anchor}\n"

    if marker not in source:
        raise ValueError(
            f"Missing env anchor: {anchor}",
        )

    return source.replace(
        marker,
        (f"{anchor}\n{line}\n"),
        1,
    )


def _apply_env() -> None:
    """Обновляет canonical runtime defaults 8.4."""
    source = ENV_PATH.read_text(
        encoding="utf-8",
    )

    replacements = {
        ("API_GATEWAY_LIFECYCLE__MAX_RUNTIME_SECONDS"): "3540",
        ("API_GATEWAY_LIFECYCLE__TASK_SOFT_TIME_LIMIT_SECONDS"): "3570",
        ("API_GATEWAY_LIFECYCLE__TASK_HARD_TIME_LIMIT_SECONDS"): "3600",
        ("API_GATEWAY_ORCHESTRATION__REQUEST_TIMEOUT_SECONDS"): "3480",
        ("ANALYSIS_SERVICE_GPU__LEASE_TIMEOUT_SECONDS"): "3300",
    }

    for key, value in replacements.items():
        source = _set_env_value(
            source,
            key=key,
            value=value,
        )

    source = source.replace(
        (
            "# Абсолютный analysis deadline меньше 30 минут.\n"
            "# 1740s application deadline -> "
            "1770s soft Celery -> 1800s hard Celery."
        ),
        (
            "# Абсолютный analysis deadline около одного часа.\n"
            "# 3540s application -> 3570s soft Celery "
            "-> 3600s hard Celery."
        ),
    )

    source = _ensure_env_after(
        source,
        anchor=("ANALYSIS_SERVICE_GPU__STATUS_TIMEOUT_SECONDS=10"),
        line=("ANALYSIS_SERVICE_PROGRESS__BASE_URL=http://api-gateway:8000"),
    )

    source = _ensure_env_after(
        source,
        anchor=("ANALYSIS_SERVICE_PROGRESS__BASE_URL=http://api-gateway:8000"),
        line=("ANALYSIS_SERVICE_PROGRESS__REQUEST_TIMEOUT_SECONDS=3"),
    )

    source = _ensure_env_after(
        source,
        anchor=("ANALYSIS_SERVICE_PROGRESS__REQUEST_TIMEOUT_SECONDS=3"),
        line=("ANALYSIS_SERVICE_PROGRESS__CONNECT_TIMEOUT_SECONDS=3"),
    )

    source = _ensure_env_after(
        source,
        anchor=("ANALYSIS_SERVICE_PIPELINE__MAX_IMAGE_BYTES=20971520"),
        line=("ANALYSIS_SERVICE_PIPELINE__MAX_STAGE_PAGES=50"),
    )

    ENV_PATH.write_text(
        source,
        encoding="utf-8",
    )


def main() -> None:
    """Применяет Stage 8.4."""
    for path in ALL_WORKFLOWS:
        workflow = _load_workflow(
            path,
        )

        settings = workflow.setdefault(
            "settings",
            {},
        )

        settings["executionTimeout"] = WORKFLOW_TIMEOUT_SECONDS

        if path == PDF_WORKFLOW:
            _apply_pdf_workflow(
                workflow,
            )

        path.write_text(
            json.dumps(
                workflow,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        print(
            f"updated: {path.relative_to(ROOT)}",
        )

    _apply_env()

    print(
        "updated: .env.example",
    )


if __name__ == "__main__":
    main()
