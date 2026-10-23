# tests/architecture/test_normative_protocol_flow.py

"""Ограниченный транспорт остаётся перед D/Finalization, отмена не читает архивы."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("mode", ["pdf", "cad", "pdf-cad"])
def test_all_large_pdf_stages_use_http_request_pagination_before_consumers(mode):
    """Обе ветви сходятся в одном resolver, без HTTP внутри Code sandbox и без top-K."""
    workflow = json.loads(
        (ROOT / f"n8n/workflows/analysis-v2-{mode}.json").read_text("utf-8")
    )
    nodes = {row["name"]: row for row in workflow["nodes"]}
    for title, source, consumer in (
        (
            "Norm Check",
            "Check Norms",
            "Use Cross-Page Consistency"
            if mode == "pdf"
            else "Merge Finding Candidates",
        ),
        ("Cross-Page", "Check Cross-Page Consistency", "Merge Cross-Page Checks"),
        ("Finalization", "Finalize Findings", "Progress Build Result"),
    ):
        if mode != "pdf" and title == "Cross-Page":
            continue
        continuation = f"Continue {source} Response"
        stored, reader, resolver = (
            f"Stored {title} Results",
            f"Read Stored {title} Results",
            f"Resolve {title} Response",
        )
        links = workflow["connections"]
        assert links[continuation]["main"][0][0]["node"] == stored
        assert links[continuation]["main"][1][0]["node"] == "Build Cancelled Result"
        assert links[stored]["main"][0][0]["node"] == reader
        assert links[stored]["main"][1][0]["node"] == resolver
        assert links[reader]["main"][0][0]["node"] == resolver
        assert consumer in [edge["node"] for edge in links[resolver]["main"][0]]
        assert nodes[reader]["type"] == "n8n-nodes-base.httpRequest"
        pagination = nodes[reader]["parameters"]["options"]["pagination"]["pagination"]
        assert pagination["paginationMode"] == "updateAParameterInEachRequest"
        assert pagination["limitPagesFetched"] is False
        assert (
            pagination["completeExpression"]
            == "={{ $response.body.next_offset === null }}"
        )
        code = nodes[resolver]["parameters"]["jsCode"]
        assert "httpRequest(" not in code and "helpers.http" not in code
        assert "ref.counts.stage_records" in code and "pending.length" in code
        assert "NODE_OPTIONS" not in code
