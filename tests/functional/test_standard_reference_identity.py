# tests/functional/test_standard_reference_identity.py

"""Адрес нормы не становится противоречием из-за темы применения или формы записи."""

from dataclasses import replace

import pytest
from pdrd_analysis_service.application.use_cases.document_context import (
    CheckCrossPageConsistency,
    DocumentContextOptions,
    DocumentPage,
    document_engineering_report,
)
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    discover_conflicts,
    document_fact_from_mapping,
    normalized_value,
)


def reference(value, quote, *, code="ГОСТ 31937-2024"):
    """Сохраняет исходные слова и явно прочитанный полный шифр."""
    return document_fact_from_mapping(
        {
            "kind": "reference",
            "subject_type": "standard",
            "identifier": code,
            "property_type": "standard_reference",
            "property_name": "Ссылка на стандарт",
            "value_raw": value,
            "evidence_text": quote,
        }
    )


def source_pair():
    """Воспроизводит разные представления одной ссылки из реального сбойного сравнения."""
    return (
        reference(
            "Обследования технического состояния",
            "Обследования технического состояния проводят специализированные организации "
            "в соответствии с ГОСТ 31937-2024",
        ),
        reference(
            "ГОСТ 31937-2024",
            "ГОСТ 31937-2024 «Здания и сооружения. Правила обследования и мониторинга технического состояния»",
        ),
    )


class NoInference:
    """Равные адреса не должны занимать GPU даже при различном описании применения."""

    async def generate_json(self, **kwargs):
        """Останавливает тест при ненужном обращении к модели."""
        raise AssertionError("Ложное сравнение ссылки направлено в VLM")


@pytest.mark.asyncio
async def test_same_literal_standard_target_preserves_both_facts_without_model_call():
    """Один адрес, две цитаты и страницы сохраняются; ложный кандидат не возникает."""
    facts = source_pair()
    observations = tuple(
        LocatedDocumentFact(page, 1, fact)
        for page, fact in zip((6, 11), facts, strict=True)
    )
    assert not discover_conflicts(observations)
    assert facts[0].value_raw != facts[1].value_raw
    assert normalized_value(facts[0]) == normalized_value(facts[1])
    pages = tuple(
        DocumentPage(o.page, o.fact.evidence_text, "", (o.fact,)) for o in observations
    )
    report = document_engineering_report(pages, "literal-standard-control")
    assert {item.observation.page for item in report.registry.evidence} == {6, 11}
    assert all(
        any(
            item.observation.fact.evidence_text == f.evidence_text
            for item in report.registry.evidence
        )
        for f in facts
    )
    options = DocumentContextOptions(4, 2, 0.5, 10, 4, 2, 500)
    findings = await CheckCrossPageConsistency(NoInference(), options).execute(
        pages=pages, document_id="literal-standard-control", engineering_report=report
    )
    assert not findings


def test_other_edition_and_conflicting_literal_target_are_not_normalized_together():
    """Тот же базовый номер с иной редакцией остаётся другой нормативной ссылкой."""
    first = source_pair()[1]
    other = reference(
        "ГОСТ 31937-2011", "Ссылка на ГОСТ 31937-2011", code="ГОСТ 31937-2011"
    )
    assert normalized_value(first) != normalized_value(other)
    inconsistent = reference(
        "ГОСТ 31937-2011",
        "ГОСТ 31937-2024 заменяет ГОСТ 31937-2011",
    )
    assert normalized_value(first) != normalized_value(inconsistent)


@pytest.mark.parametrize(
    "change",
    [
        {"property_name": "Наименование стандарта"},
        {"property_type": "unknown_reference_property"},
        {
            "evidence_text": "Обследования технического состояния проводят специализированные организации"
        },
        {
            "evidence_text": "Обследования технического состояния: ГОСТ 31937-2024 и СП 255.1325800.2016"
        },
        {"identifier": "ГОСТ 31937"},
    ],
)
def test_reference_identity_does_not_replace_title_unknown_property_or_ambiguous_evidence(
    change,
):
    """Нормализация адреса требует подходящего свойства и однозначной буквальной цитаты."""
    first, second = source_pair()
    assert normalized_value(replace(first, **change)) != normalized_value(second)
