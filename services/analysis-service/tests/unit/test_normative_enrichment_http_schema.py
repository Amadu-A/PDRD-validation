# services/analysis-service/tests/unit/test_normative_enrichment_http_schema.py

"""HTTP contract tests normative enrichment TZ-5.2."""

from pdrd_analysis_service.transport.http.schemas import (
    FinalizeRequest,
)


def test_finalize_request_accepts_normative_candidates() -> None:
    """Finalize contract сохраняет enrichment N metadata."""
    request = FinalizeRequest(
        findings=[],
        experience_by_finding={},
        normative_candidates=[
            {
                "source_id": "NQ1",
                "point_id": "point-1",
                "score": 0.91,
                "document_id": "document-1",
                "section_id": "section-1",
                "category_id": None,
                "source_sha256": "a" * 64,
                "source_file": "GOST.pdf",
                "source_path": None,
                "page": 17,
                "chunk_index": 4,
                "text": "Тестовое нормативное требование.",
            }
        ],
    )

    assert (
        len(
            request.normative_candidates,
        )
        == 1
    )

    source = request.normative_candidates[0].to_domain()

    assert source.source_id == "NQ1"
    assert source.document_id == "document-1"
    assert source.source_file == "GOST.pdf"
    assert source.page == 17


def test_finalize_request_keeps_legacy_compatibility() -> None:
    """Старый caller может не передавать enrichment candidates."""
    request = FinalizeRequest(
        findings=[],
        experience_by_finding={},
    )

    assert request.normative_candidates == []
