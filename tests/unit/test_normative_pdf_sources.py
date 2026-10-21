# tests/unit/test_normative_pdf_sources.py

"""Границы отображения N-реквизитов не ломают экспорт старого результата."""

import pytest
from pdrd_api_gateway.application.normative_pdf_sources import normative_pdf_fields


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        42,
        {"clause": None, "normative_status": 42},
        {"clause": "4.2", "normative_status": {"evidence_ids": "broken"}},
    ],
)
def test_optional_or_damaged_metadata_does_not_invent_normative_status_or_crash_pdf(
    metadata,
):
    """Отсутствующий статус явно неизвестен, исходная цитата и адрес остаются доступны."""
    source = {
        "source_id": "N-old",
        "page": 7,
        "text": "Исходная цитата",
        "document_id": "00000000-0000-4000-8000-000000000001",
        "atomic_requirement": metadata,
    }
    fields = normative_pdf_fields([source], public_origin="https://example.test")
    assert len(fields) == 1 and "Исходная цитата" in fields[0].value
    assert fields[0].href.endswith("/content#page=7")
    if isinstance(metadata, dict):
        assert "не подтверждена" in fields[0].value


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "https://user:password@example.test",
        "https://example.test?target=x",
        "https://example.test#fragment",
    ],
)
def test_only_configured_clean_public_origin_becomes_pdf_link(origin):
    """Адрес приложения не строится из недоверенного request Host или произвольного URL."""
    source = {
        "source_id": "N-old",
        "page": 7,
        "text": "Цитата",
        "document_id": "00000000-0000-4000-8000-000000000001",
    }
    assert normative_pdf_fields([source], public_origin=origin)[0].href is None
