# tests/functional/test_human_semantic_evaluation.py

"""Приёмка требует реальных находок и явного смыслового решения, а не общих слов."""

from copy import deepcopy

import pytest

from scripts.evaluate_human_remarks import evaluate


def sample():
    """Создаёт независимый минимальный пример; это не экспертная матрица контрольного PDF."""
    matrix = [
        {
            "hc_id": key,
            "human_remark": "Обосновать расход",
            "context_used": True,
            "context_pages": [15, 20],
            "coords": {"remark": [1, 2], "mistake": [3, 4]},
        }
        for key in ("A", "B")
    ]
    findings = [
        {
            "finding_id": "real",
            "page": 15,
            "comment": "Обосновать расход подпитки",
            "evidence": "0,5205 л/с",
        }
    ]
    decisions = [
        {
            "hc_id": key,
            "verdict": "matched",
            "reason": "Тот же контур и расчётное обоснование расхода",
            "matches": [
                {
                    "finding_id": "real",
                    "pages": [15],
                    "quote": "Обосновать расход подпитки",
                    "object_match": True,
                    "property_match": True,
                    "action_match": True,
                }
            ],
        }
        for key in ("A", "B")
    ]
    return matrix, findings, decisions


def test_semantic_report_counts_annotations_and_unique_questions_without_changing_matrix():
    """Две экспертные аннотации могут относиться к одному предметному вопросу."""
    matrix, findings, decisions = sample()
    original = deepcopy(matrix)
    result = evaluate(matrix, findings, decisions, same_questions=[("A", "B")])
    assert result["annotations"] == {"total": 2, "matched": 2}
    assert result["unique_questions"] == {"total": 1, "matched": 1}
    assert matrix == original
    assert result["cases"][0]["matrix_case"] == matrix[0]


@pytest.mark.parametrize(
    "change",
    ["missing_decision", "fake_finding", "fake_page", "fake_quote", "word_only"],
)
def test_invented_or_keyword_only_match_does_not_pass_validation(change):
    """Оценка без проверяемой предметной связи не считается успешной приёмкой."""
    matrix, findings, decisions = sample()
    if change == "missing_decision":
        decisions.pop()
    else:
        match = decisions[0]["matches"][0]
        if change == "fake_finding":
            match["finding_id"] = "made-up"
        if change == "fake_page":
            match["pages"] = [31]
        if change == "fake_quote":
            match["quote"] = "Придуманное замечание"
        if change == "word_only":
            match["object_match"] = False
    with pytest.raises(ValueError):
        evaluate(matrix, findings, decisions)
