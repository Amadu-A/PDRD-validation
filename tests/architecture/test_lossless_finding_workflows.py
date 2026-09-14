# tests/architecture/test_lossless_finding_workflows.py

"""Architecture guards lossless finding pipeline."""

import json
from pathlib import Path
from typing import Any

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

WORKFLOW_PATHS = (
    ROOT / "n8n" / "workflows" / "analysis-v2-pdf.json",
    ROOT / "n8n" / "workflows" / "analysis-v2-cad.json",
    ROOT / "n8n" / "workflows" / "analysis-v2-pdf-cad.json",
)

RESULT_NODE_BY_WORKFLOW = {
    "analysis-v2-pdf.json": "Build Page Result",
    "analysis-v2-cad.json": "Build CAD Result",
    "analysis-v2-pdf-cad.json": "Build PDF CAD Result",
}


def _workflow(
    path: Path,
) -> dict[str, Any]:
    """Читает workflow JSON."""
    payload = json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )

    assert isinstance(
        payload,
        dict,
    )

    return payload


def _node_code(
    workflow: dict[str, Any],
    node_name: str,
) -> str:
    """Возвращает jsCode указанного Code node."""
    nodes = workflow.get(
        "nodes",
        [],
    )

    assert isinstance(
        nodes,
        list,
    )

    for node in nodes:
        if not isinstance(
            node,
            dict,
        ):
            continue

        if (
            node.get(
                "name",
            )
            != node_name
        ):
            continue

        parameters = node.get(
            "parameters",
            {},
        )

        assert isinstance(
            parameters,
            dict,
        )

        return str(
            parameters.get(
                "jsCode",
                "",
            )
        )

    raise AssertionError(
        f"Node {node_name!r} отсутствует.",
    )


def test_merge_never_semantically_filters_generated_findings() -> None:
    """Merge только объединяет N/engineering и T-first candidates."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        code = _node_code(
            workflow,
            "Merge Finding Candidates",
        )

        assert "...normativeFindings" in code, path
        assert "...technicalAssignmentFindings" in code, path
        assert ".filter(" not in code, path
        assert "confidence" not in code.lower(), path


def test_result_nodes_take_all_finalization_findings() -> None:
    """Result builder не создаёт shortlist после finalization."""
    for path in WORKFLOW_PATHS:
        workflow = _workflow(
            path,
        )

        code = _node_code(
            workflow,
            RESULT_NODE_BY_WORKFLOW[path.name],
        )

        assert "finalization.findings" in code, path

        if path.name == "analysis-v2-cad.json":
            assert "Array.isArray(finalization.findings)" in code, path
            assert "findings_count: findings.length" in code, path
            continue

        assert "(finalization.findings ?? []).map(" in code, path

        if path.name == "analysis-v2-pdf-cad.json":
            assert "findings_count: findings.length" in code, path
        else:
            # PDF сначала собирает findings одного листа в Build Page Result.
            # Общий findings_count считается позже в Aggregate PDF Result.
            assert "findings," in code, path


def test_pdf_aggregate_counts_every_page_finding() -> None:
    """PDF aggregate flatMap-ит page findings без semantic shortlist."""
    workflow = _workflow(
        ROOT / "n8n" / "workflows" / "analysis-v2-pdf.json",
    )

    code = _node_code(
        workflow,
        "Aggregate PDF Result",
    )

    assert "flatMap" in code
    assert "page.findings" in code
    assert "findings_count: findings.length" in code


def test_finalization_source_contains_per_finding_fallback() -> None:
    """Finalization обязана вернуть fallback при пропуске finding моделью."""
    path = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "application"
        / "use_cases"
        / "finalization.py"
    )

    source = path.read_text(
        encoding="utf-8",
    )

    assert "item = returned.get(" in source
    assert "if item is None:" in source
    assert "self._fallback(" in source
    assert "except VisionModelError as error:" in source


def test_high_recall_budget_is_committed_in_settings_and_env() -> None:
    """High-recall normative pass получает единый увеличенный output budget."""
    settings_path = (
        ROOT
        / "services"
        / "analysis-service"
        / "src"
        / "pdrd_analysis_service"
        / "core"
        / "settings.py"
    )

    settings_source = settings_path.read_text(
        encoding="utf-8",
    )

    environment_source = (ROOT / ".env.example").read_text(
        encoding="utf-8",
    )

    assert (
        "norm_check_num_predict: int = Field(\n"
        "        default=14000," in settings_source
    )

    assert (
        "max_retry_num_predict: int = Field(\n        default=14000," in settings_source
    )

    assert (
        "ANALYSIS_SERVICE_PIPELINE__NORM_CHECK_NUM_PREDICT=14000" in environment_source
    )

    assert "ANALYSIS_SERVICE_VISION__MAX_RETRY_NUM_PREDICT=14000" in environment_source
