# services/analysis-service/tests/unit/test_understanding_modes.py

"""Защищает лёгкий режим снимком main и проверяет включение D по выбранному лимиту."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pdrd_analysis_service.application.json_schemas import build_page_facts_schema
from pdrd_analysis_service.application.prompts import (
    build_finalization_prompt,
    build_normative_check_prompt,
    build_page_understanding_prompt,
)
from pdrd_analysis_service.domain.analysis import DocumentContextSource

from .test_analysis_use_cases import finding, page_facts

BASELINE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/page-understanding-light.json"
    ).read_text(encoding="utf-8")
)


def test_disabled_d_restores_approved_main_understanding():
    """Промпт и схема без D точно совпадают со снимком прежнего лёгкого Understanding."""
    assert (
        build_page_understanding_prompt(**BASELINE["arguments"], max_facts=0)
        == BASELINE["prompt"]
    )
    assert build_page_facts_schema(0) == BASELINE["schema"]


@pytest.mark.parametrize("limit", [1, 4, 12])
def test_enabled_d_uses_configured_fact_limit(limit):
    """Положительный лимит включает атомарные факты, сохраняя основные поля листа."""
    schema = build_page_facts_schema(limit)
    assert schema["properties"]["document_facts"]["maxItems"] == limit
    assert "document_facts" in schema["required"]
    for name, field in BASELINE["schema"]["properties"].items():
        assert schema["properties"][name] == field
    prompt = build_page_understanding_prompt(**BASELINE["arguments"], max_facts=limit)
    assert f"до {limit} атомарных document_facts" in prompt
    assert "Не считай похожие теги одним объектом" in prompt


@pytest.mark.parametrize("enabled", [False, True])
def test_requirements_and_finalization_only_receive_supplied_d_evidence(enabled):
    """Пустой D не утяжеляет промпты; сохранённый D остаётся в N/T/U и финализации."""
    source = DocumentContextSource("D-p0010-c0", 10, evidence_text="Б-012 -35 °C")
    sources = (source,) if enabled else ()
    requirements = build_normative_check_prompt(
        page_number=7,
        extracted_text="Б-012 -37 °C",
        page_facts=page_facts(),
        normative_sources=(),
        normative_text_limit=500,
        document_context_sources=sources,
    )
    finalization = build_finalization_prompt(
        findings=(replace(finding(), document_context_basis_sources=sources),),
        experience_by_finding={},
        experience_context_limit=500,
    )
    if enabled:
        assert "DOCUMENT CONTEXT SOURCES:" in requirements
        assert source.source_id in requirements and source.evidence_text in requirements
        assert "document_context_basis_sources" in finalization
        assert source.source_id in finalization and source.evidence_text in finalization
    else:
        assert "DOCUMENT CONTEXT SOURCES:" not in requirements
        assert "document_context_source_ids" not in requirements
        assert "document_context_basis_sources" not in finalization
