# services/analysis-service/tests/unit/test_duplicate_positions.py

"""Регрессии: восстановление обозначений, пропущенных VLM."""

from pdrd_analysis_service.application.use_cases.common import (
    ViolationCandidateSelection,
    select_violation_candidates,
)
from pdrd_analysis_service.application.use_cases.duplicate_positions import (
    duplicate_position_id,
    is_protected_duplicate_id,
    recover_duplicate_positions,
    repeated_standalone_positions,
)

PAGE22 = """Принципиальная схема топливоснабжения
8.9.1
8.9.2
8.9.3
8.2.1
8.9.1
8.9.2
8.9.3
"""


def _selection(*candidates: dict) -> ViolationCandidateSelection:
    return ViolationCandidateSelection(
        candidates=tuple(candidates),
        source_indexes_by_candidate=tuple((i,) for i in range(1, len(candidates) + 1)),
        generated_count=len(candidates),
    )


def _candidate(comment: str, evidence: str) -> dict:
    return {
        "comment": comment,
        "evidence": evidence,
        "category": "marking",
        "severity": "warning",
        "status": "needs_review",
        "visual_regions": [{"x_min": 620, "y_min": 440, "x_max": 660, "y_max": 470}],
    }


def test_recovery_is_the_same_whether_vlm_saw_tag_or_not() -> None:
    """Пропуск VLM не меняет восстановленное замечание о повторе."""
    seen = _candidate(
        "Дублирование позиционного обозначения 8.9.3 на насосах 8.2.1 и 8.2.2.",
        "Недоказанная VLM связь с насосами.",
    )
    missed = recover_duplicate_positions(
        selection=_selection(),
        extracted_text=PAGE22,
        page_type="Схема",
        page_number=22,
    )
    found = recover_duplicate_positions(
        selection=_selection(seen),
        extracted_text=PAGE22,
        page_type="Схема",
        page_number=22,
    )

    def find_tag(result, tag):
        return [
            item
            for item in result.selection.candidates
            if item.get("__duplicate_position_tag") == tag
        ]

    left = find_tag(missed, "8.9.3")
    right = find_tag(found, "8.9.3")
    assert len(left) == len(right) == 1
    assert left == right
    assert left[0]["status"] == "needs_review"
    assert left[0]["normative_source_ids"] == []
    assert left[0]["visual_regions"] == []
    assert missed.selection.represented_count == missed.selection.generated_count
    assert found.selection.represented_count == found.selection.generated_count
    assert duplicate_position_id(22, "8.9.3") == "p22-dpos-8-9-3"
    assert is_protected_duplicate_id("p22-dpos-8-9-3")


def test_pymupdf_sorted_rows_recover_page_22_position_labels() -> None:
    """В одной PDF-строке могут оказаться несколько разнесённых подписей."""
    extracted_text = "\n".join(
        (
            "22" + " " * 71 + "Принципиальная схема топливоснабжения",
            "        8.9.3        8.3.9        8.9.1        PG        8.5.6",
            "        8.4.8        8.9.2        LE",
            "        8.9.1",
            "        8.3.1        8.1.1        8.9.3",
            "        8.4.21        инв.        8.9.2",
            "Позиция 8.9.4 упомянута в примечании.",
            "Повторная ссылка на 8.9.4 в примечании.",
        )
    )
    expected = (("8.9.1", 2), ("8.9.2", 2), ("8.9.3", 2))
    assert repeated_standalone_positions(extracted_text, page_type="чертёж") == expected

    recovered = recover_duplicate_positions(
        selection=_selection(),
        extracted_text=extracted_text,
        page_type="чертёж",
        page_number=22,
    )
    assert recovered.tags == tuple(tag for tag, _ in expected)
    assert recovered.synthetic_count == 3
    assert any(
        candidate.get("__duplicate_position_tag") == "8.9.3"
        for candidate in recovered.selection.candidates
    )


def test_comparisons_with_same_numbers_keep_distinct_instruments() -> None:
    """Одинаковые пары номеров не склеивают датчик PE с термометром TG."""
    selection = select_violation_candidates(
        [
            _candidate(
                "Несоответствие позиционных номеров датчика PE: 8.5.1 и 8.5.5.",
                "У датчика PE видны оба номера 8.5.1 и 8.5.5.",
            ),
            _candidate(
                "Несоответствие позиционных номеров термометра TG: 8.5.1 и 8.5.5.",
                "У термометра TG видны оба номера 8.5.1 и 8.5.5.",
            ),
        ]
    )
    assert selection.generated_count == selection.consolidated_count == 2
    assert selection.source_indexes_by_candidate == ((1,), (2,))


def test_multi_position_duplicate_wording_keeps_distinct_instruments() -> None:
    """Слово «повтор» не склеивает разные приборы с парой номеров."""
    selection = select_violation_candidates(
        [
            _candidate(
                "Повтор позиционных номеров датчика PE 8.5.1 и 8.5.5.",
                "Для датчика PE на схеме указаны 8.5.1 и 8.5.5.",
            ),
            _candidate(
                "Повтор позиционных номеров термометра TG 8.5.1 и 8.5.5.",
                "Для термометра TG на схеме указаны 8.5.1 и 8.5.5.",
            ),
        ]
    )
    assert selection.generated_count == selection.consolidated_count == 2


def test_repeated_vlm_paraphrases_collapse_without_losing_raw_provenance() -> None:
    """Перефразировки объединяются с сохранением исходных индексов."""
    candidates = _selection(
        _candidate(
            "Дублирование позиционного обозначения 8.9.2.",
            "Около двух насосов.",
        ),
        _candidate("Повтор позиционного номера 8.9.2.", "Другие координаты."),
        _candidate("Независимое противоречие Т1.1 и Т2.1.", "Два разных значения."),
    )
    recovered = recover_duplicate_positions(
        selection=candidates,
        extracted_text=PAGE22,
        page_type="scheme",
        page_number=22,
    )
    matches = [
        (index, candidate)
        for index, candidate in enumerate(recovered.selection.candidates)
        if candidate.get("__duplicate_position_tag") == "8.9.2"
    ]
    assert len(matches) == 1
    assert recovered.selection.source_indexes_by_candidate[matches[0][0]] == (1, 2)
    assert recovered.selection.represented_count == recovered.selection.generated_count
    assert any(
        "Т1.1" in candidate["comment"] for candidate in recovered.selection.candidates
    )


def test_no_hypothetical_duplicates_from_specification_or_prose() -> None:
    """Ссылки в спецификации и прозе не подтверждают повтор на схеме."""
    text = "Спецификация\n8.9.3\nПозиция 8.9.3 в тексте\n8.9.3\n"
    assert repeated_standalone_positions(text, page_type="specification") == ()
    text = "Принципиальная схема\n8.9.3\nУпоминание 8.9.3 в комментарии\n"
    assert repeated_standalone_positions(text, page_type="Схема") == ()


def test_no_false_claim_of_confirmed_violation() -> None:
    """Текстовый повтор остаётся фактом для проверки инженером."""
    result = recover_duplicate_positions(
        selection=_selection(),
        extracted_text=PAGE22,
        page_type="scheme",
        page_number=22,
    )
    for item in result.selection.candidates:
        assert item["status"] == "needs_review"
        assert "проверить" in item["comment"].casefold()
        assert not item["normative_source_ids"]


def test_unverified_vlm_duplicate_is_rejected_with_provenance() -> None:
    """Одна подпись или выдуманная позиция не становится замечанием о повторе."""
    selection = _selection(
        _candidate("Позиционное обозначение 8.11.3 повторяется.", "Две области."),
        _candidate("Позиционное обозначение 8.12.34 повторяется.", "Две области."),
    )
    result = recover_duplicate_positions(
        selection=selection,
        extracted_text="Принципиальная схема\n8.11.3\n",
        page_type="scheme",
        page_number=22,
    )

    assert result.selection.candidates == ()
    assert result.selection.generated_count == 2
    assert result.selection.represented_count == 0
    assert result.selection.rejected_reasons == (
        "unverified_duplicate_position",
        "unverified_duplicate_position",
    )


def test_unverified_duplicate_rule_is_limited_to_schemes() -> None:
    """Повтор номера вне схемы проходит прежнюю семантическую проверку."""
    selection = _selection(
        _candidate("Позиционное обозначение 8.11.3 повторяется.", "Таблица."),
    )
    result = recover_duplicate_positions(
        selection=selection,
        extracted_text="Спецификация\n8.11.3\n",
        page_type="specification",
        page_number=22,
    )
    assert result.selection.candidates == selection.candidates


def test_project_note_context_cannot_manufacture_second_drawing_label() -> None:
    """Контекст пояснительной записки не создаёт подпись на листе."""
    augmented = (
        "=== ТЕКСТ АНАЛИЗИРУЕМОЙ СТРАНИЦЫ ===\n"
        "Принципиальная схема\n8.9.3\n"
        "\n=== РЕЛЕВАНТНЫЙ КОНТЕКСТ ПОЯСНИТЕЛЬНОЙ ЗАПИСКИ ===\n"
        "8.9.3\n8.9.3\n"
    )
    assert repeated_standalone_positions(augmented, page_type="scheme") == ()


def test_source_backed_candidate_is_never_replaced_by_source_less_review() -> None:
    """Кандидат с N-источником сохраняется отдельно от текстового факта."""
    original = _candidate(
        "Дублирование позиционного обозначения 8.9.3.",
        "На листе видны две разные позиции; применимость нормы требует проверки.",
    )
    original["normative_source_ids"] = ["N1"]
    result = recover_duplicate_positions(
        selection=_selection(original),
        extracted_text=PAGE22,
        page_type="scheme",
        page_number=22,
    )
    assert any(
        candidate.get("normative_source_ids") == ["N1"]
        for candidate in result.selection.candidates
    )
    assert any(
        candidate.get("__duplicate_position_tag") == "8.9.3"
        for candidate in result.selection.candidates
    )
    assert result.selection.represented_count == result.selection.generated_count


def test_recovery_does_not_silently_truncate_more_than_32_distinct_tags() -> None:
    """При большом числе повторов нельзя терять последние обозначения."""
    tags = tuple(f"8.9.{number}" for number in range(1, 41))
    text = "\n".join(("Принципиальная схема", *tags, *tags))
    recovered = recover_duplicate_positions(
        selection=_selection(),
        extracted_text=text,
        page_type="Схема",
        page_number=22,
    )
    assert len(recovered.tags) == len(tags)
    assert recovered.synthetic_count == len(tags)
    assert "8.9.40" in recovered.tags
    assert recovered.selection.represented_count == len(tags)
