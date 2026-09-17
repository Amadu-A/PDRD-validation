# services/analysis-service/tests/unit/test_combined_mode_prompts.py

"""Regression tests semantics режима PDF + CAD."""

from pdrd_analysis_service.application.prompts import (
    COMBINED_MODE_SEMANTICS,
    build_normative_check_prompt,
    build_page_understanding_prompt,
)
from pdrd_analysis_service.domain.analysis import (
    PageFacts,
)


def _combined_text() -> str:
    """Возвращает combined PDF/CAD context."""
    return (
        "[PDF_TEXT]\n"
        "PDF sheet text\n\n"
        "[CAD_MACHINE_CONTEXT]\n"
        "CAD entities and connections"
    )


def _facts() -> PageFacts:
    """Возвращает минимальный PageFacts."""
    return PageFacts(
        discipline="ЭОМ",
        page_type="scheme",
        summary="Принципиальная схема",
        objects=(),
        connections=(),
        labels=(),
        normative_queries=(),
    )


def test_understanding_receives_combined_mode_semantics() -> None:
    """Combined understanding знает, что PDF и CAD — один лист."""
    prompt = build_page_understanding_prompt(
        page_number=1,
        heuristic_page_type="scheme",
        extracted_text=_combined_text(),
    )

    assert COMBINED_MODE_SEMANTICS in prompt
    assert "ОДНОГО И ТОГО ЖЕ" in prompt
    assert "CAD не является нормативным источником" in prompt


def test_normative_check_receives_combined_mode_semantics() -> None:
    """Normative check не превращает CAD в нормативное доказательство."""
    prompt = build_normative_check_prompt(
        page_number=1,
        extracted_text=_combined_text(),
        page_facts=_facts(),
        normative_sources=(),
        normative_text_limit=700,
    )

    assert COMBINED_MODE_SEMANTICS in prompt
    assert "CAD не может создавать N/T/U source_id" in prompt


def test_pdf_only_prompt_does_not_receive_combined_semantics() -> None:
    """Обычный PDF workflow не получает лишнюю combined инструкцию."""
    prompt = build_page_understanding_prompt(
        page_number=1,
        heuristic_page_type="scheme",
        extracted_text="Обычный PDF текст.",
    )

    assert COMBINED_MODE_SEMANTICS not in prompt


def test_global_prompt_distinguishes_signal_labels_from_equipment() -> None:
    """Сигнальные/net labels не обязаны находиться в перечне элементов."""
    prompt = build_normative_check_prompt(
        page_number=1,
        extracted_text=_combined_text(),
        page_facts=_facts(),
        normative_sources=(),
        normative_text_limit=700,
    )

    assert '"24V DC"' in prompt
    assert '"AC OK"' in prompt
    assert '"GND"' in prompt
    assert "Перечне элементов" in prompt
