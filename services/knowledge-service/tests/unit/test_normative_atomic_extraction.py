# services/knowledge-service/tests/unit/test_normative_atomic_extraction.py

"""Регрессии атомизации, контекста и доказательного нормативного статуса без моделей."""

from dataclasses import replace
from uuid import uuid4

import pytest
from pdrd_knowledge_service.domain.normative_indexing import NormativeTextPage
from pdrd_knowledge_service.domain.normative_requirement_extraction import (
    extract_normative_requirements,
)
from pdrd_knowledge_service.domain.normative_requirements import (
    NormativeStatusEvidence,
    requirement_status,
)

from ..atomic_normative_support import (
    ACTOR_ID,
    DOCUMENT_ID,
    PAGES,
    document,
    proof_payload,
)


def extracted(text, **changes):
    """Передаёт нативный нормативный текст, не готовые атомарные требования."""
    return tuple(
        extract_normative_requirements(
            **{
                "document_id": DOCUMENT_ID,
                "source_sha256": document().sha256,
                "source_file": "source.pdf",
                "pages": (NormativeTextPage(1, "СП 99.13330.2026\n" + text),),
                **changes,
            }
        )
    )


def evidence(**changes):
    """Фиктивное свидетельство не используется как нормативный статус реального корпуса."""
    return NormativeStatusEvidence(
        **{
            **proof_payload(),
            "evidence_id": str(uuid4()),
            "document_id": str(DOCUMENT_ID),
            "source_sha256": document().sha256,
            "verified_by": str(ACTOR_ID),
            "recorded_at": "2026-10-20T12:00:00+00:00",
            **changes,
        }
    )


def test_one_clause_has_independent_atoms_with_exact_full_source_and_coordinates():
    """Отдельные предписания сохраняют общие условия исходного пункта и не выдумывают bbox."""
    rows = extracted(
        "5.1 Для насоса следует предусматривать резерв. Следует предусматривать сигнализацию."
    )
    atoms = [row for row in rows if row.clause == "5.1"]
    assert len(atoms) == 2 and atoms[0].requirement_id != atoms[1].requirement_id
    assert "резерв" in atoms[0].text and "сигнализац" in atoms[1].text
    for item in atoms:
        assert item.source_text == atoms[0].source_text
        assert item.text in item.source_text
        for part in item.source_fragments:
            page = (
                "СП 99.13330.2026\n5.1 Для насоса следует предусматривать резерв. "
                "Следует предусматривать сигнализацию."
            )
            assert page[part.start : part.end] == part.text
        assert item.document_code == "СП 99.13330.2026" and item.edition == "2026"


@pytest.mark.parametrize("separator", ["\n", " "])
@pytest.mark.parametrize(
    "verb,modality", [("Следует", "mandatory"), ("Рекомендуется", "advisory")]
)
def test_list_items_inherit_header_without_inventing_quote_or_losing_subclauses(
    separator, verb, modality
):
    """Модальность шапки относится к обоим подпунктам, но наследование требует проверки."""
    rows = [
        row
        for row in extracted(
            f"5.1 {verb} предусматривать:{separator}а) резерв;{separator}б) сигнализацию."
        )
        if row.clause == "5.1"
    ]
    assert len(rows) == 2 and [row.subclause for row in rows] == ["а", "б"]
    assert all(row.modality == modality and row.text in row.source_text for row in rows)
    assert all(
        "inherited_prescription_requires_review" in row.limitations for row in rows
    )
    assert all(
        any(f"{verb} предусматривать:" in part for part in row.context) for row in rows
    )
    scoped = evidence(clause_scope=("5.1.а",))
    assert (
        requirement_status(rows[1], (scoped,), "2026-10-20")["edition_status"]
        == "unknown"
    )


def test_wrapped_prohibition_is_not_a_permitted_alternative():
    """Перенос строки между «не» и «допускается» не меняет смысл нормативного запрета."""
    item = next(
        row
        for row in extracted("5.1 Для насоса не\nдопускается отключение резерва.")
        if row.clause == "5.1"
    )
    assert item.modality == "mandatory" and item.verification_type == "action"


@pytest.mark.parametrize(
    "text",
    [
        "5.1 При необходимости насос отключают.",
        "5.1 Описание должностных обязанностей оператора насоса.",
        "3 Термины и определения\n3.1 Система: оборудование, которое должно работать совместно.",
        "Приложение А\n(справочное)\nА.1 Пример: насос должен иметь резерв.",
    ],
)
def test_definitions_informative_appendix_and_word_roots_are_not_mandatory(text):
    """Справочный контекст и части слов не создают обязательного нормативного предписания."""
    rows = [row for row in extracted(text) if row.clause]
    assert rows and all(row.modality == "informative" for row in rows)
    assert all(row.extraction_status == "needs_review" for row in rows)


def test_normative_section_after_definitions_is_not_suppressed():
    """Область справочного заголовка заканчивается при переходе к нормативному разделу."""
    rows = extracted(
        "3 Термины и определения\n3.1 Система: оборудование, которое должно работать совместно.\n"
        "4 Общие требования\n4.1 Насос должен иметь резерв."
    )
    assert next(row for row in rows if row.clause == "3.1").modality == "informative"
    assert next(row for row in rows if row.clause == "4.1").modality == "mandatory"


def test_continuation_keeps_conditions_exceptions_and_each_physical_source_page():
    """Продолжение не становится самостоятельной нормой без исходного условия."""
    rows = extracted("", pages=PAGES)
    item = next(row for row in rows if row.clause == "5.2")
    assert [part.page for part in item.source_fragments] == [1, 2]
    assert item.applicability_conditions and item.exceptions
    assert "временного назначения" in item.exceptions[0]
    assert "обоснования" in item.text
    for part in item.source_fragments:
        assert PAGES[part.page - 1].text[part.start : part.end] == part.text


def test_table_does_not_swallow_following_sections_or_unsupported_prescriptions():
    """После таблицы сохраняются также справочные пункты, новый раздел и приложение."""
    rows = extracted(
        "5.1 Следует принимать по таблице 2.\nТаблица 2\n0.1 10\n1.2 20\n"
        "5.2 Пояснение специального случая.\n6.1 Следует предусматривать резерв.\n"
        "А.1 Следует выполнить расчёт."
    )
    assert {"5.1", "5.2", "6.1", "А.1"} <= {row.clause for row in rows}
    assert not any(row.clause in {"0.1", "1.2"} for row in rows)
    assert next(row for row in rows if row.clause == "5.2").modality == "informative"


def test_wrapped_normative_references_do_not_hide_following_clauses_after_table():
    """Формат СП 8: перенос ссылки на СП/пункт не поглощает следующие разделы."""
    rows = extracted(
        "5.6 Расход для складов по СП\n114.13330 следует принимать по таблице 5.\n"
        "Таблица 5\n0.1 10\n5.7 Следует выполнить расчёт насоса.\n"
        "7.2 Резервуары размещают в соответствии с пунктом\n10.4 настоящего свода правил.\n"
        "7.3 Насос должен иметь резерв.\n10.4 Резервуары должны иметь подъезды.\n"
        "12.1 Следует предусматривать сигнализацию."
    )
    assert {row.clause for row in rows} == {
        "",
        "5.6",
        "5.7",
        "7.2",
        "7.3",
        "10.4",
        "12.1",
    }
    assert "114.13330" in next(row for row in rows if row.clause == "5.6").source_text
    assert (
        "10.4 настоящего"
        in next(row for row in rows if row.clause == "7.2").source_text
    )
    assert all(
        "настоящего" not in row.source_text for row in rows if row.clause == "10.4"
    )


def test_unsupported_sentence_and_blank_source_page_are_not_discarded():
    """Неизвестная форма нормы и пустая физическая страница остаются в происхождении."""
    pages = (
        NormativeTextPage(
            1, "СП 99.13330.2026\n5.1 Насос должен иметь резерв. Режим выбирают"
        ),
        NormativeTextPage(2, ""),
        NormativeTextPage(3, "по специальной классификации."),
    )
    rows = [row for row in extracted("", pages=pages) if row.clause == "5.1"]
    assert len(rows) == 2 and any("Режим выбирают" in row.text for row in rows)
    for row in rows:
        assert [part.page for part in row.source_fragments] == [1, 2, 3]
        assert row.source_fragments[1].text == ""
        assert "source_pages_missing" not in row.limitations


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Расход воды должен быть не менее 10 л/с.", "numeric_limit"),
        ("Объём следует определять по формуле V = Q * t.", "formula"),
        (
            "Расход следует принимать по таблице 2. Таблица 2\nЗдание | Расход\nПримечание — для объектов класса А.",
            "table",
        ),
        ("Здания следует относить к категории А по назначению.", "classification"),
        ("Следует предусматривать сигнализацию.", "action"),
        ("В состав системы должны входить резервные насосы.", "system_composition"),
        ("Следует выполнить расчёт обоснования.", "calculation_required"),
        (
            "Если предусмотрены насосы и резервуары, необходимо предусмотреть сигнал.",
            "conditional",
        ),
        (
            "Вместо резервуара допускается применять альтернативное решение.",
            "alternative",
        ),
        (
            "Нумерация листов должна соответствовать ведомости документации.",
            "documentation",
        ),
    ],
)
def test_all_required_requirement_families_are_retained(text, kind):
    """Классификация формы не означает готовность исполнять составную норму."""
    item = next(row for row in extracted("5.1 " + text) if row.clause == "5.1")
    assert item.verification_type == kind
    if kind in {"table", "formula", "classification", "conditional", "alternative"}:
        assert item.extraction_status == "needs_review"
        assert "structured_interpretation_required" in item.limitations


def test_table_keeps_notes_footnotes_and_continuation_without_numeric_row_atoms():
    """Числа таблицы нельзя выделять в нормы отдельно от заголовка и примечаний."""
    pages = (
        NormativeTextPage(
            1,
            "СП 99.13330.2026\n5.1 Следует принимать значения по таблице 2.\nТаблица 2\nКласс | Расход\n0.1 10\n",
        ),
        NormativeTextPage(
            2,
            "1.2 20\nПримечание — только при условии А.\n* Кроме временных объектов.\n"
            "5.2 Следует предусматривать резерв.",
        ),
    )
    rows = extracted("", pages=pages)
    item = next(row for row in rows if row.clause == "5.1")
    assert "1.2 20" in item.source_text and "Примечание" in item.source_text
    assert "Кроме временных" in item.source_text
    assert [part.page for part in item.source_fragments] == [1, 2]
    assert not any(row.clause in {"0.1", "1.2"} for row in rows)


@pytest.mark.parametrize(
    "text",
    [
        "Рекомендуется предусматривать резерв.",
        "Для сведения приведены примеры.",
        "Допускается применять альтернативу.",
    ],
)
def test_recommendations_permissions_and_explanations_are_not_mandatory(text):
    """Даже подтверждённый статус документа не меняет модальность справочного фрагмента."""
    item = next(row for row in extracted("5.1 " + text) if row.clause == "5.1")
    assert item.modality != "mandatory" and item.extraction_status == "needs_review"
    status = requirement_status(item, (evidence(),), "2026-10-20")
    assert status["requirement_binding"] != "mandatory"


def test_ids_repeat_and_change_for_different_source_edition_and_extractor():
    """Повторная индексация стабильна, разные редакции и версии извлечения не смешиваются."""
    source = "5.1 Насос должен иметь резерв."
    original = extracted(source)
    assert original == extracted(source)
    assert {r.requirement_id for r in original}.isdisjoint(
        r.requirement_id for r in extracted(source, source_sha256="b" * 64)
    )
    assert {r.requirement_id for r in original}.isdisjoint(
        r.requirement_id for r in extracted(source, extractor_version="next-version")
    )
    newer = extracted(
        source, pages=(NormativeTextPage(1, "СП 99.13330.2027\n" + source),)
    )
    assert {r.requirement_id for r in original}.isdisjoint(
        r.requirement_id for r in newer
    )


def test_incomplete_clause_and_missing_identity_remain_reviewable_without_filename_inference():
    """Неполная цитата сохраняется, но год в имени файла не подтверждает редакцию."""
    item = extracted(
        "",
        source_file="СП 99.13330.2026.pdf",
        pages=(NormativeTextPage(2, "5.1 Насос должен иметь"),),
    )[0]
    assert item.source_text == "5.1 Насос должен иметь"
    assert item.document_code == item.edition == ""
    assert {"document_edition_unidentified", "clause_end_unverified"} <= set(
        item.limitations
    )


def test_ready_is_not_normative_currency_and_proof_is_scoped_and_date_bounded():
    """Редакция, обязательность и применимость к проекту не подменяют друг друга."""
    item = next(
        row
        for row in extracted("5.1 Насос должен иметь резерв.")
        if row.clause == "5.1"
    )
    assert requirement_status(item, (), "2026-10-20")["edition_status"] == "unknown"
    status = requirement_status(item, (evidence(),), "2026-10-20")
    assert (
        status["edition_status"] == "current"
        and status["requirement_binding"] == "mandatory"
    )
    assert status["project_applicability"] == "not_assessed"
    for unrelated in (
        replace(item, clause="5.10"),
        replace(item, source_sha256="b" * 64),
        replace(item, edition="2025"),
    ):
        assert (
            requirement_status(unrelated, (evidence(),), "2026-10-20")["edition_status"]
            == "unknown"
        )
    assert (
        requirement_status(item, (evidence(),), "2027-01-01")["edition_status"]
        == "unknown"
    )
    conflicting = (evidence(), evidence(edition_status="withdrawn"))
    assert (
        requirement_status(item, conflicting, "2026-10-20")["edition_status"]
        == "disputed"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_url": "file:///local"},
        {"source_url": "https://user:secret@example.org/"},
        {"verified_by": ""},
        {"source_quote": ""},
        {"clause_scope": ()},
        {"valid_from": "2027-01-01", "valid_until": "2026-01-01"},
        {"confirmed_on": "2099-01-01"},
    ],
)
def test_status_rejects_unverifiable_or_forged_evidence(changes):
    """Проверяемость не заменяется произвольным статусом из JSON."""
    with pytest.raises(ValueError):
        evidence(**changes)


def test_dependencies_preserve_real_codes_and_numbered_targets_without_ordinary_words():
    """Аудит настоящих СП выявил ложное «пунктов с» и потерю ссылки СП 48.13330."""
    rows = extracted(
        "6.1.17 Следует составлять акты по СП 48.13330 с учетом пункта 8.9, "
        "таблицы А.1 и приложения Б. Указанные требования пунктов с условиями применения."
    )
    row = next(row for row in rows if row.clause == "6.1.17")
    assert "СП 48.13330" in row.normative_dependencies
    assert "пункта 8.9" in row.normative_dependencies
    assert "таблицы А.1" in row.normative_dependencies
    assert "приложения Б" in row.normative_dependencies
    assert "пунктов с" not in row.normative_dependencies
    assert row.text in row.source_text
