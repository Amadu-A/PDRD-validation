# services/knowledge-service/tests/unit/test_technical_assignment_atomic_requirements.py

"""Unit tests high-recall atomization ТЗ."""

import pytest
from pdrd_knowledge_service.domain.technical_assignment_indexing import (
    extract_technical_assignment_requirements,
    infer_requirement_scopes,
)


@pytest.mark.parametrize(
    "text",
    [
        "Предусмотреть АВР.",
        "Установить УЗО.",
        "Предусмотреть автоматический ввод резерва.",
    ],
)
def test_short_explicit_requirement_is_not_length_filtered(text):
    """Предписание и предмет важнее числа символов; цитата и порядок сохраняются."""
    first = extract_technical_assignment_requirements(page_number=7, text=text)
    assert len(first) == 1
    assert first[0].source_text == text
    assert first[0].page_number == 7 and first[0].ordinal == 1
    assert first[0].strength == "explicit"
    assert first == extract_technical_assignment_requirements(page_number=7, text=text)


@pytest.mark.parametrize(
    "text",
    [
        "АВР",
        "УЗО",
        "Предусмотреть.",
        "Установить || 12.",
        "1 || А | 2",
        "Общие положения",
    ],
)
def test_short_noise_or_heading_is_not_a_requirement(text):
    """Одиночная аббревиатура, заголовок и OCR-мусор не создают предписания."""
    assert extract_technical_assignment_requirements(page_number=7, text=text) == ()


def test_short_table_requirement_retains_header_and_literal_row():
    """Заголовок и предмет короткой строки остаются частью цитаты для T-first."""
    text = "Электроснабжение\nАВР | Предусмотреть.\nУЗО | Установить."
    requirements = extract_technical_assignment_requirements(page_number=3, text=text)
    assert len(requirements) == 2
    assert requirements[0].source_text == "Электроснабжение АВР | Предусмотреть."
    assert requirements[1].source_text == "УЗО | Установить."
    assert all(r.strength == "explicit" for r in requirements)


def test_extracts_explicit_requirement_with_normative_reference() -> None:
    """Explicit T requirement сохраняет source, scope и N reference."""
    text = (
        "2.4 Электроснабжение\n"
        "Предусмотреть питание по двум независимым вводам "
        "с автоматическим вводом резерва согласно "
        "СП 256.1325800.2016. "
        "Расчетную мощность принять по техническим условиям."
    )

    requirements = extract_technical_assignment_requirements(
        page_number=4,
        text=text,
    )

    assert (
        len(
            requirements,
        )
        >= 2
    )

    first = requirements[0]

    assert first.page_number == 4

    assert first.ordinal == 1

    assert first.local_key == "p4-r1"

    assert first.strength == "explicit"

    assert "electrical" in first.scopes

    assert first.normative_refs == ("СП 256.1325800.2016",)

    assert "двум независимым вводам" in first.text


def test_declarative_requirement_candidate_is_not_removed() -> None:
    """Declarative T facts остаются candidates до T-first validation."""
    text = (
        "Топливо: основное — природный газ. "
        "Резервное топливо — дизельное топливо. "
        "Система трубопроводов — четырехтрубная."
    )

    requirements = extract_technical_assignment_requirements(
        page_number=2,
        text=text,
    )

    texts = tuple(requirement.text for requirement in requirements)

    assert (
        len(
            requirements,
        )
        == 3
    )

    assert "Топливо: основное — природный газ." in texts

    assert "Резервное топливо — дизельное топливо." in texts

    assert "Система трубопроводов — четырехтрубная." in texts

    assert all(requirement.strength == "candidate" for requirement in requirements)


def test_ocr_line_hyphenation_is_repaired_before_atomization() -> None:
    """Перенос OCR не разрывает слово requirement."""
    text = (
        "Проектом предусмотреть блочно-модульную котель-\n"
        "ную с автоматизированными котлами отечественного "
        "производства."
    )

    requirements = extract_technical_assignment_requirements(
        page_number=2,
        text=text,
    )

    assert (
        len(
            requirements,
        )
        == 1
    )

    requirement = requirements[0]

    assert "котельную" in requirement.text

    assert "котель- ную" not in requirement.text

    assert requirement.strength == "explicit"

    assert "thermal" in requirement.scopes


def test_multiple_scope_hints_are_preserved() -> None:
    """Scope hints не принуждают requirement к одной дисциплине."""
    scopes = infer_requirement_scopes(
        "Предусмотреть электроснабжение щита автоматизации и диспетчеризации."
    )

    assert "electrical" in scopes

    assert "automation" in scopes


def test_unknown_scope_is_general_not_rejected() -> None:
    """Неизвестная дисциплина остаётся general candidate."""
    scopes = infer_requirement_scopes(
        "Предусмотреть поставку комплектного оборудования."
    )

    assert scopes == ("general",)


def test_short_ocr_noise_is_not_indexed_as_requirement() -> None:
    """Очевидный OCR noise не создаёт бесполезный vector point."""
    requirements = extract_technical_assignment_requirements(
        page_number=1,
        text="1 | | 2 |",
    )

    assert requirements == ()
