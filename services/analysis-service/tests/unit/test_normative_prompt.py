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


def normalize_prompt(
    value: str,
) -> str:
    """Нормализует регистр и пробелы для semantic prompt assertions."""
    return " ".join(
        value.casefold().split(),
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


def test_prompt_supports_independent_engineering_findings_without_sources() -> None:
    """Prompt сохраняет independent engineering analysis без N/T/U."""
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text="Чистый исходный проект без замечаний проверяющего.",
        page_facts=make_page_facts(),
        normative_sources=(),
        normative_text_limit=1000,
        normative_system_prompt=None,
    )

    assert "даже в случае" in prompt

    assert "если N/T/U SOURCES полностью пусты" in prompt

    assert "Основной рабочий сценарий" in prompt

    assert "БЕЗ заранее нанесённых" in prompt

    assert "самостоятельно проверь инженерную" in prompt

    assert "внутренние противоречия" in prompt

    assert "несогласованную маркировку" in prompt

    assert "логические противоречия схемы" in prompt

    assert "status=needs_review" in prompt

    assert "Не подавляй конкретное engineering finding" in prompt

    assert "только потому, что для него не найден N/T/U source." in prompt


def test_active_section_prompt_cannot_weaken_global_evidence_rules() -> None:
    """Section prompt уточняет N-проверку, но не меняет evidence semantics."""
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text="Тестовый лист.",
        page_facts=make_page_facts(),
        normative_sources=(),
        normative_text_limit=1000,
        normative_system_prompt=(
            "Проверяй лист ТОЛЬКО по приведённым нормативным фрагментам."
        ),
    )

    normalized = normalize_prompt(
        prompt,
    )

    assert "роль active section system prompt" in normalized

    assert "не может превращать отсутствие n-source" in normalized

    assert "не отменяет независимый engineering / visual" in normalized

    assert "при конфликте этих правил" in normalized


def test_source_less_finding_requires_direct_internal_evidence() -> None:
    """Source-less candidate допускается только для внутренне доказуемого факта."""
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text="Тестовый лист.",
        page_facts=make_page_facts(),
        normative_sources=(),
        normative_text_limit=1000,
        normative_system_prompt=None,
    )

    normalized = normalize_prompt(
        prompt,
    )

    assert "перед source-less finding обязательно проверь" in normalized

    assert "один и тот же объект" in normalized

    assert "одна и та же характеристика" in normalized

    assert "одному и тому же смысловому scope" in normalized

    assert "пустая ячейка таблицы" in normalized

    assert "needs_review не является разрешением" in normalized

    assert "source-less режим не является fallback" in normalized

    assert "если эти условия не доказаны самим листом" in normalized


def test_external_normative_claim_requires_real_normative_source() -> None:
    """Модель не должна объявлять внешнее нормативное требование без N."""
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text="На листе указан ГОСТ.",
        page_facts=make_page_facts(),
        normative_sources=(),
        normative_text_limit=1000,
        normative_system_prompt=None,
    )

    normalized = normalize_prompt(
        prompt,
    )

    assert '"не соответствует нормативу"' in normalized

    assert '"норматив требует"' in normalized

    assert '"стандарт устарел"' in normalized

    assert '"стандарт заменён"' in normalized

    assert "только тогда, когда реально переданный" in normalized

    assert "не создавай нормативное утверждение из памяти модели" in normalized

    assert "само по себе не доказывает" in normalized


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
