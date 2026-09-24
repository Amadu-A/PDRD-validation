# services/analysis-service/tests/unit/test_finding_quality_guardrails.py

"""Regression tests semantic finding-quality guardrails."""

from pdrd_analysis_service.application.prompts import (
    build_finalization_prompt,
    build_normative_check_prompt,
)
from pdrd_analysis_service.domain.analysis import (
    FindingDraft,
    PageFacts,
)


def _page_facts() -> PageFacts:
    """Возвращает минимальный инженерный context."""
    return PageFacts(
        discipline="Тепломеханические решения",
        page_type="scheme",
        summary=("Схема оборудования."),
        objects=(
            "Насосы",
            "Виброкомпенсаторы",
        ),
        connections=(),
        labels=(),
        normative_queries=(),
    )


def _finding(
    *,
    comment: str,
    evidence: str,
) -> FindingDraft:
    """Возвращает source-less candidate для finalization prompt."""
    return FindingDraft(
        finding_id="p9-f1",
        page=9,
        page_type="scheme",
        category="scheme_logic",
        severity="warning",
        status="needs_review",
        comment=comment,
        evidence=evidence,
        recommendation_draft=("Проверить проектное решение."),
        confidence=0.75,
        normative_source_ids=(),
        basis="",
        basis_sources=(),
        experience_query=comment,
    )


def _normalize(
    value: str,
) -> str:
    """Нормализует prompt для устойчивых assertions."""
    return " ".join(
        value.casefold().split(),
    )


def test_normative_prompt_separates_physical_page_from_design_sheet() -> None:
    """Backend page number не является проектным полем Лист."""
    prompt = build_normative_check_prompt(
        page_number=9,
        extracted_text=("Лист 5. Текст проектной документации."),
        page_facts=_page_facts(),
        normative_sources=(),
        normative_text_limit=700,
        normative_system_prompt=None,
    )

    normalized = _normalize(
        prompt,
    )

    assert "physical pdf page index" in normalized

    assert 'transport metadata, не проектное поле "лист"' in normalized

    assert "не используй его как значение поля" in normalized

    assert "physical pdf page/page_number" in normalized

    assert "служебной metadata" in normalized


def test_normative_prompt_requires_explicit_quantity_relationship() -> None:
    """Разное количество разных объектов не является finding само по себе."""
    prompt = build_normative_check_prompt(
        page_number=25,
        extracted_text=("Насосы — 3 шт. Виброкомпенсаторы — 4 шт. Краны — 12 шт."),
        page_facts=_page_facts(),
        normative_sources=(),
        normative_text_limit=700,
        normative_system_prompt=None,
    )

    normalized = _normalize(
        prompt,
    )

    assert "разное количество разных типов оборудования" in normalized

    assert "обязательную связь между этими количествами" in normalized

    assert "разность количества двух классов оборудования" in normalized

    assert "не доказывает ошибку" in normalized


def test_finalization_uses_physical_pdf_page_only_as_metadata() -> None:
    """Finalizer не должен повторно легализовать PDF-page vs Лист."""
    prompt = build_finalization_prompt(
        findings=(
            _finding(
                comment=("Физическая страница 9 не совпадает с полем Лист 5."),
                evidence=("Backend page=9, в основной надписи указано Лист 5."),
            ),
        ),
        experience_by_finding={},
        experience_context_limit=600,
    )

    normalized = _normalize(
        prompt,
    )

    assert '"physical_pdf_page":9' in prompt

    assert "важная семантика metadata" in normalized

    assert "не является автоматически проектным полем" in normalized

    assert "нельзя считать различие между physical_pdf_page" in normalized

    assert "candidate сравнивает physical_pdf_page" in normalized


def test_finalization_rejects_unsupported_quantity_relationship() -> None:
    """Quantity mismatch требует доказанной инженерной связи."""
    prompt = build_finalization_prompt(
        findings=(
            _finding(
                comment=(
                    "Количество виброкомпенсаторов не совпадает с количеством насосов."
                ),
                evidence=("Виброкомпенсаторы — 4 шт., насосы — 3 шт."),
            ),
        ),
        experience_by_finding={},
        experience_context_limit=600,
    )

    normalized = _normalize(
        prompt,
    )

    assert "разное количество разных типов оборудования" in normalized

    assert "4 виброкомпенсатора против 3 насосов" in normalized

    assert "такие числа сами по себе не доказывают ошибку" in normalized

    assert "если сама связь x↔y придумана моделью" in normalized


def test_finalization_keeps_direct_same_object_mismatch_possible() -> None:
    """Guardrail не запрещает реальное противоречие одного объекта."""
    prompt = build_finalization_prompt(
        findings=(
            _finding(
                comment=("Количество позиции Н1 указано по-разному."),
                evidence=("Для позиции Н1 на схеме 3 шт., в спецификации 4 шт."),
            ),
        ),
        experience_by_finding={},
        experience_context_limit=600,
    )

    normalized = _normalize(
        prompt,
    )

    assert "разные значения количества одного" in normalized

    assert "идентифицированного объекта/позиции" in normalized

    assert "candidate может быть keep" in normalized


def test_discovery_keeps_explicitly_related_objects_and_repeated_labels() -> None:
    """Связанные элементы и точный повтор маркировки остаются кандидатами."""
    prompt = build_normative_check_prompt(
        page_number=22,
        extracted_text="Схема: позиция 8.9.2 указана у двух элементов.",
        page_facts=_page_facts(),
        normative_sources=(),
        normative_text_limit=700,
    )

    normalized = _normalize(prompt)
    assert "документ явно задаёт обязательную связь" in normalized
    assert "точное повторение одной позиционной подписи" in normalized
    assert "конкретным фактом для needs_review" in normalized
    assert "не доказывает само по себе нарушение нормы" in normalized
    assert "разное количество разных типов оборудования" in normalized
    assert "обязательную связь между этими количествами" in normalized


def test_finalization_treats_retrieval_score_as_recall_only() -> None:
    """Similarity score не заменяет semantic N verification."""
    prompt = build_finalization_prompt(
        findings=(
            _finding(
                comment=("Проверить обозначение оборудования."),
                evidence=("На листе видны разные обозначения одного элемента."),
            ),
        ),
        experience_by_finding={},
        experience_context_limit=600,
    )

    normalized = _normalize(
        prompt,
    )

    assert "retrieval score" in normalized

    assert "используются только для recall" in normalized

    assert "не доказывает" in normalized

    assert "тематического сходства недостаточно" in normalized
