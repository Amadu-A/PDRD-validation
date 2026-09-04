# services/analysis-service/tests/unit/test_user_package_context.py

"""Unit tests separation user-package context from normative basis."""

from pdrd_analysis_service.application.prompts import (
    build_normative_check_prompt,
)
from pdrd_analysis_service.domain.analysis import (
    NormativeSource,
    PageFacts,
    UserPackageSource,
)


def make_page_facts() -> PageFacts:
    """Создаёт test page facts."""
    return PageFacts(
        discipline="КИПиА",
        page_type="Схема автоматизации",
        summary="Шкаф управления насосом.",
        objects=("Шкаф управления",),
        connections=("Сигнальный кабель",),
        labels=("ША-1",),
        normative_queries=("требования к шкафу управления",),
    )


def make_normative_source() -> NormativeSource:
    """Создаёт N-source."""
    return NormativeSource(
        source_id="N1",
        point_id="n-point",
        score=0.9,
        document_id="normative-id",
        section_id="section-id",
        category_id=None,
        source_sha256="a" * 64,
        source_file="ГОСТ.pdf",
        source_path=None,
        page=10,
        chunk_index=1,
        text="Нормативное требование.",
    )


def make_user_source() -> UserPackageSource:
    """Создаёт U-source."""
    return UserPackageSource(
        source_id="U1",
        point_id="u-point",
        score=0.88,
        document_id="package-id",
        section_id="section-id",
        category_id="package-category",
        source_sha256="b" * 64,
        source_file="Требования заказчика.pdf",
        source_path=None,
        page=4,
        chunk_index=0,
        text="Заказчик требует исполнение IP54.",
    )


def test_prompt_keeps_package_sources_in_separate_block() -> None:
    """U-source виден модели, но отделён от нормативной evidence."""
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text="ША-1",
        page_facts=make_page_facts(),
        normative_sources=(make_normative_source(),),
        user_package_sources=(make_user_source(),),
        normative_text_limit=1000,
    )

    assert "USER PACKAGE SOURCES:" in prompt

    assert "NORMATIVE SOURCES:" in prompt

    assert '"source_id":"U1"' in prompt

    assert '"source_id":"N1"' in prompt

    assert "USER PACKAGE SOURCES являются пользовательским" in prompt

    assert "НЕ являются" in prompt

    assert "нормативными документами" in prompt


def test_super_system_forbids_u_ids_as_normative_basis() -> None:
    """Prompt явно запрещает U-id в normative_source_ids."""
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text="ША-1",
        page_facts=make_page_facts(),
        normative_sources=(make_normative_source(),),
        user_package_sources=(make_user_source(),),
        normative_text_limit=1000,
    )

    assert "U1, U2 и далее запрещено возвращать" in prompt

    assert "normative_source_ids" in prompt

    assert "нормативным доказательством являются только" in prompt


def test_prompt_without_packages_remains_compatible() -> None:
    """Legacy analysis получает пустой package context."""
    prompt = build_normative_check_prompt(
        page_number=3,
        extracted_text="ША-1",
        page_facts=make_page_facts(),
        normative_sources=(make_normative_source(),),
        normative_text_limit=1000,
    )

    assert "USER PACKAGE SOURCES:" in prompt

    assert "USER PACKAGE SOURCES:\n[]" in prompt
