# services/analysis-service/tests/unit/test_normative_prompt.py

"""Unit tests managed normative system prompt."""

from pdrd_analysis_service.application.prompts import (
    LEGACY_SECTION_SYSTEM_PROMPT,
    NORMATIVE_SUPER_SYSTEM_PROMPT,
    build_normative_check_prompt,
)
from pdrd_analysis_service.domain.analysis import (
    NormativeSource,
    PageFacts,
)
from pdrd_analysis_service.transport.http.schemas import (
    NormativeSourcePayload,
)


def make_page_facts() -> PageFacts:
    """Создаёт минимальные test page facts."""
    return PageFacts(
        discipline="ЭОМ",
        page_type="scheme",
        summary="Схема защитного заземления.",
        objects=("Щит",),
        connections=("PE",),
        labels=("PE",),
        normative_queries=("защитное заземление",),
    )


def make_source() -> NormativeSource:
    """Создаёт managed normative source."""
    return NormativeSource(
        source_id="N1",
        point_id="point-1",
        score=0.91,
        source_file="GOST.pdf",
        source_path=None,
        page=7,
        chunk_index=1,
        text="Требование нормативного документа.",
        document_id="document-1",
        section_id="section-1",
        category_id=None,
        source_sha256="a" * 64,
    )


def test_prompt_contains_super_system_and_exact_active_prompt() -> None:
    """Final prompt содержит immutable super-system и exact section prompt."""
    active_prompt = "  CUSTOM ACTIVE PROMPT\nDo not trim this line.  "

    prompt = build_normative_check_prompt(
        page_number=1,
        extracted_text="Текст листа.",
        page_facts=make_page_facts(),
        normative_sources=(make_source(),),
        normative_text_limit=1000,
        normative_system_prompt=active_prompt,
    )

    assert NORMATIVE_SUPER_SYSTEM_PROMPT in prompt

    assert active_prompt in prompt

    assert '"document_id":"document-1"' in prompt

    assert '"source_sha256":"' in prompt


def test_explicit_empty_active_prompt_does_not_restore_legacy_prompt() -> None:
    """Пустой override остаётся пустым, а не подменяется default prompt."""
    prompt = build_normative_check_prompt(
        page_number=1,
        extracted_text="Текст.",
        page_facts=make_page_facts(),
        normative_sources=(make_source(),),
        normative_text_limit=1000,
        normative_system_prompt="",
    )

    assert LEGACY_SECTION_SYSTEM_PROMPT not in prompt

    assert (
        "--- ACTIVE SECTION SYSTEM PROMPT ---\n\n"
        "--- END ACTIVE SECTION SYSTEM PROMPT ---"
    ) in prompt


def test_none_prompt_uses_legacy_compatibility_fallback() -> None:
    """Старый n8n request без snapshot пока продолжает работать."""
    prompt = build_normative_check_prompt(
        page_number=1,
        extracted_text="Текст.",
        page_facts=make_page_facts(),
        normative_sources=(make_source(),),
        normative_text_limit=1000,
        normative_system_prompt=None,
    )

    assert LEGACY_SECTION_SYSTEM_PROMPT in prompt


def test_managed_normative_source_payload_roundtrip() -> None:
    """Analysis HTTP boundary принимает новые Knowledge metadata."""
    payload = NormativeSourcePayload(
        source_id="N1",
        point_id="point-1",
        score=0.9,
        document_id="document-1",
        section_id="section-1",
        category_id="category-1",
        source_sha256="b" * 64,
        source_file="GOST.pdf",
        source_path=None,
        page=12,
        chunk_index=2,
        text="Normative text.",
    )

    source = payload.to_domain()

    assert source.document_id == "document-1"
    assert source.section_id == "section-1"
    assert source.category_id == "category-1"
    assert source.source_sha256 == "b" * 64
