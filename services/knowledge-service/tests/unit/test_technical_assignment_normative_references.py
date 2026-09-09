# services/knowledge-service/tests/unit/test_technical_assignment_normative_references.py

"""Unit tests совместимости ссылок ТЗ и имён managed N."""

from datetime import (
    UTC,
    datetime,
)
from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.technical_assignment_retrieval import (
    SearchTechnicalAssignmentGuidedNormative,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    IndexingStatus,
    NormativeDocument,
)
from pdrd_knowledge_service.domain.technical_assignment import (
    PDF_MIME_TYPE,
)
from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    canonicalize_normative_reference,
    extract_normative_references,
)
from pdrd_knowledge_service.domain.technical_assignment_retrieval import (
    NormativeReferenceResolutionStatus,
)

NOW = datetime(
    2026,
    9,
    7,
    12,
    0,
    tzinfo=UTC,
)


def _document(
    original_name: str,
) -> NormativeDocument:
    """Создаёт READY normative document с реальным filename-style."""
    document_id = uuid4()

    return NormativeDocument(
        document_id=document_id,
        section_id=uuid4(),
        category_id=None,
        original_name=original_name,
        storage_key=f"{document_id}.pdf",
        mime_type=PDF_MIME_TYPE,
        size_bytes=1024,
        sha256="a" * 64,
        index_status=IndexingStatus.READY,
        index_error=None,
        indexed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        area=CatalogArea.NORMATIVE,
    )


@pytest.mark.parametrize(
    (
        "reference",
        "original_name",
    ),
    (
        (
            "СП 76.13330.2016",
            "01_SP_76.13330_2016_Electrical_Devices_Installation.pdf",
        ),
        (
            "СП 77.13330.2016",
            "02_SP_77.13330_2016_Automation_Systems_Installation.pdf",
        ),
        (
            "ГОСТ 12.2.007.0-75",
            "01_GOST_12.2.007.0_75_Electrical_Safety.pdf",
        ),
        (
            "ГОСТ 21.408-2013",
            "01_GOST_21.408_2013_Automation_Documentation.pdf",
        ),
        (
            "ПУЭ 6",
            "01_PUE_6_Edition_Full.pdf",
        ),
        (
            "ПУЭ 7",
            "02_PUE_7_Edition_Full.pdf",
        ),
        (
            "СТО 11233753-001-2006",
            "03_STO_11233753_001_2006_Automation_Adjustment.doc",
        ),
    ),
)
def test_russian_reference_matches_real_catalog_filename(
    reference: str,
    original_name: str,
) -> None:
    """Русская ссылка совпадает с латинизированным managed filename."""
    normalized_reference = canonicalize_normative_reference(
        reference,
    )

    normalized_filename = canonicalize_normative_reference(
        original_name,
    )

    assert normalized_reference

    assert normalized_reference in normalized_filename


@pytest.mark.parametrize(
    (
        "reference",
        "original_name",
    ),
    (
        (
            "СП 76.13330.2016",
            "01_SP_76.13330_2016_Electrical_Devices_Installation.pdf",
        ),
        (
            "ГОСТ 12.2.007.0-75",
            "01_GOST_12.2.007.0_75_Electrical_Safety.pdf",
        ),
        (
            "ПУЭ 6",
            "01_PUE_6_Edition_Full.pdf",
        ),
        (
            "СТО 11233753-001-2006",
            "03_STO_11233753_001_2006_Automation_Adjustment.doc",
        ),
    ),
)
def test_resolver_resolves_real_catalog_filename(
    reference: str,
    original_name: str,
) -> None:
    """TZ-4 resolver реально использует canonical mapping."""
    document = _document(
        original_name,
    )

    resolution = SearchTechnicalAssignmentGuidedNormative._resolve_reference(
        reference,
        (document,),
    )

    assert resolution.status is NormativeReferenceResolutionStatus.RESOLVED

    assert resolution.reference == reference

    assert resolution.document_ids == (
        str(
            document.document_id,
        ),
    )

    assert resolution.source_files == (original_name,)


def test_extractor_supports_russian_and_ascii_prefixes() -> None:
    """Extractor понимает обе формы нормативных обозначений."""
    text = (
        "Применить СП 76.13330.2016. "
        "Дополнительно проверить GOST 21.408-2013. "
        "Руководствоваться ПУЭ 7 и STO 11233753-001-2006."
    )

    references = extract_normative_references(
        text,
    )

    assert references == (
        "СП 76.13330.2016",
        "GOST 21.408-2013",
        "ПУЭ 7",
        "STO 11233753-001-2006",
    )


def test_extractor_deduplicates_language_aliases() -> None:
    """СП и SP одного номера не превращаются в две ссылки."""
    references = extract_normative_references(
        "Применить СП 76.13330.2016. The same document is SP 76.13330.2016."
    )

    assert references == ("СП 76.13330.2016",)


def test_pue_edition_is_part_of_canonical_key() -> None:
    """ПУЭ 6 и ПУЭ 7 остаются разными managed references."""
    pue_6 = canonicalize_normative_reference(
        "ПУЭ 6",
    )

    pue_7 = canonicalize_normative_reference(
        "PUE 7",
    )

    assert pue_6 == "PUE6"

    assert pue_7 == "PUE7"

    assert pue_6 != pue_7
