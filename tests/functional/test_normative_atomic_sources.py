# tests/functional/test_normative_atomic_sources.py

"""Реальные физические страницы СП → атомарный каталог без модельных цитат и ложной актуальности."""

import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from pdrd_knowledge_service.domain.normative_indexing import NormativeTextPage
from pdrd_knowledge_service.domain.normative_requirement_extraction import (
    extract_normative_requirements,
)
from pdrd_knowledge_service.domain.normative_requirements import requirement_status

SOURCES = json.loads(
    (
        Path(__file__).parents[1]
        / "fixtures/engineering/normative-atomic-source-pages.json"
    ).read_text("utf-8")
)


@pytest.mark.parametrize("source", SOURCES, ids=lambda item: item["source_file"])
def test_native_normative_clauses_keep_exact_pdf_pages_dependencies_and_unknown_legal_status(
    source,
):
    """Гидранты, пожарный объём и гидравлика приходят из оригинала, без ожидаемых замечаний во входе."""
    pages = tuple(NormativeTextPage(**page) for page in source["pages"])
    arguments = {
        "document_id": uuid5(NAMESPACE_URL, source["source_sha256"]),
        "source_sha256": source["source_sha256"],
        "source_file": source["source_file"],
        "pages": pages,
    }
    requirements = tuple(extract_normative_requirements(**arguments))
    assert requirements == tuple(extract_normative_requirements(**arguments))
    assert set(source["selected_clauses"]) <= {row.clause for row in requirements}
    assert len({row.requirement_id for row in requirements}) == len(requirements)
    by_page = {page.page_number: page.text for page in pages}
    for clause in source["selected_clauses"]:
        atoms = [row for row in requirements if row.clause == clause]
        for atom in atoms:
            assert atom.source_sha256 == source["source_sha256"]
            assert atom.document_code.startswith("СП ") and len(atom.edition) == 4
            assert atom.text in atom.source_text and clause in atom.source_text
            for part in atom.source_fragments:
                assert by_page[part.page][part.start : part.end] == part.text
            status = requirement_status(atom, (), "2026-10-20")
            assert (
                status["edition_status"] == status["requirement_binding"] == "unknown"
            )
            assert status["project_applicability"] == "not_assessed"
