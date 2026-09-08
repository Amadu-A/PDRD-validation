# tests/architecture/test_normative_enrichment_pipeline.py

"""Architecture guards non-destructive normative enrichment TZ-5.2."""

import json
from pathlib import Path

PROJECT_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

WORKFLOW_PATH = PROJECT_ROOT / "n8n" / "workflows" / "analysis-v2-pdf.json"

SCHEMA_PATH = (
    PROJECT_ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "transport"
    / "http"
    / "schemas.py"
)

ROUTES_PATH = (
    PROJECT_ROOT
    / "services"
    / "analysis-service"
    / "src"
    / "pdrd_analysis_service"
    / "transport"
    / "http"
    / "routes.py"
)


def _load_workflow() -> dict[str, object]:
    """Читает PDF workflow."""
    return json.loads(
        WORKFLOW_PATH.read_text(
            encoding="utf-8",
        )
    )


def _nodes_by_name(
    workflow: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Индексирует workflow nodes по name."""
    nodes = workflow["nodes"]

    assert isinstance(
        nodes,
        list,
    )

    return {
        node["name"]: node
        for node in nodes
        if isinstance(
            node,
            dict,
        )
    }


def _next_node(
    workflow: dict[str, object],
    source_name: str,
) -> str:
    """Возвращает main successor node."""
    connections = workflow["connections"]

    assert isinstance(
        connections,
        dict,
    )

    source = connections[source_name]

    assert isinstance(
        source,
        dict,
    )

    main = source["main"]

    assert isinstance(
        main,
        list,
    )

    branch = main[0]

    assert isinstance(
        branch,
        list,
    )

    connection = branch[0]

    assert isinstance(
        connection,
        dict,
    )

    node = connection["node"]

    assert isinstance(
        node,
        str,
    )

    return node


def test_pdf_workflow_has_non_destructive_normative_enrichment() -> None:
    """PDF pipeline ищет N повторно после появления findings."""
    workflow = _load_workflow()

    nodes = _nodes_by_name(
        workflow,
    )

    assert "Prepare Finding Normative Queries" in nodes
    assert "Search Finding Norms" in nodes

    assert (
        _next_node(
            workflow,
            "Check Norms",
        )
        == "Prepare Finding Normative Queries"
    )

    assert (
        _next_node(
            workflow,
            "Prepare Finding Normative Queries",
        )
        == "Search Finding Norms"
    )

    assert (
        _next_node(
            workflow,
            "Search Finding Norms",
        )
        == "Prepare Experience Queries"
    )

    prepare_parameters = nodes["Prepare Finding Normative Queries"]["parameters"]

    assert isinstance(
        prepare_parameters,
        dict,
    )

    prepare_code = str(prepare_parameters["jsCode"])

    assert "finding.experience_query" in prepare_code

    assert ".slice(0, 10)" in prepare_code

    search_parameters = nodes["Search Finding Norms"]["parameters"]

    assert isinstance(
        search_parameters,
        dict,
    )

    assert search_parameters["url"] == (
        "http://pdrd-knowledge-service:8401/internal/v1/search/normative"
    )

    search_body = str(search_parameters["body"])

    assert "normative_section_id" in search_body

    assert "normative_document_ids" in search_body

    prepare_experience_parameters = nodes["Prepare Experience Queries"]["parameters"]

    assert isinstance(
        prepare_experience_parameters,
        dict,
    )

    prepare_experience_code = str(prepare_experience_parameters["jsCode"])

    assert "normative_candidates" in prepare_experience_code

    # Enrichment IDs не сталкиваются с primary N1/N2.
    assert "NQ${index + 1}" in prepare_experience_code

    build_map_parameters = nodes["Build Experience Map"]["parameters"]

    assert isinstance(
        build_map_parameters,
        dict,
    )

    build_map_code = str(build_map_parameters["jsCode"])

    assert "normative_candidates" in build_map_code

    finalize_parameters = nodes["Finalize Findings"]["parameters"]

    assert isinstance(
        finalize_parameters,
        dict,
    )

    finalize_body = str(finalize_parameters["body"])

    assert "normative_candidates" in finalize_body

    page_parameters = nodes["Build Page Result"]["parameters"]

    assert isinstance(
        page_parameters,
        dict,
    )

    page_code = str(page_parameters["jsCode"])

    assert "Normalize Requirement Search" in page_code

    assert "normative_enrichment" in page_code

    aggregate_parameters = nodes["Aggregate PDF Result"]["parameters"]

    assert isinstance(
        aggregate_parameters,
        dict,
    )

    aggregate_code = str(aggregate_parameters["jsCode"])

    assert "finding_normative_search" in aggregate_code


def test_finalize_transport_passes_normative_candidates() -> None:
    """HTTP transport проводит N candidates до application use case."""
    schema_text = SCHEMA_PATH.read_text(
        encoding="utf-8",
    )

    routes_text = ROUTES_PATH.read_text(
        encoding="utf-8",
    )

    assert "normative_candidates: list[" in schema_text

    assert "NormativeSourcePayload" in schema_text

    assert "normative_candidates=tuple(" in routes_text

    assert "request.normative_candidates" in routes_text
