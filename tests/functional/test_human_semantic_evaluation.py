# tests/functional/test_human_semantic_evaluation.py

"""Приёмка требует реальных находок и явного смыслового решения, а не общих слов."""

from copy import deepcopy

import pytest

from scripts.evaluate_human_remarks import evaluate, finding_pages, load_matrix


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


def test_sheet_corpus_keeps_empty_enabled_remarks_and_excludes_only_boolean_false():
    """Не улучшает процент удалением неполных, неопределённых или неудобных примеров."""
    payload = {
        "file_name": "control.pdf",
        "items": [
            {
                "sheet": 15,
                "comments": [
                    {"area": "Фрагмент", "comment": "Обосновать расход", "flag": True},
                    {"area": "Поле", "comment": "", "flag": True},
                    {"comment": "Не проверять", "flag": False},
                    {"comment": "Сохранить", "flag": "False"},
                ],
            }
        ],
    }
    original = deepcopy(payload)
    rows, excluded = load_matrix(payload)
    assert excluded == 1
    assert len(rows) == 3
    assert rows[1]["human_remark"] == ""
    assert {row["sheet"] for row in rows} == {15}
    assert len({row["hc_id"] for row in rows}) == 3
    assert payload == original


@pytest.mark.parametrize("status", ["hypothesis", "rejected", "not_evaluable"])
def test_diagnostic_or_wrong_sheet_does_not_count_as_reproduction(status):
    """Сходство формулировок не компенсирует отсутствие замечания на нужном листе."""
    matrix, findings, decisions = sample()
    matrix[0]["sheet"] = 24
    with pytest.raises(ValueError, match="заданном листе"):
        evaluate(matrix, findings, decisions)
    matrix[0]["sheet"] = 15
    findings[0]["status"] = status
    with pytest.raises(ValueError, match="Диагностическая гипотеза"):
        evaluate(matrix, findings, decisions)


def test_reference_and_checked_pages_do_not_count_as_issue_locations():
    """Образец реквизита и факт чтения страницы не повышают полноту воспроизведения."""
    finding = {
        "page": 9,
        "checked_pages": [1, 2, 3, 9],
        "document_context_basis_sources": [{"page": 1}, {"page": 2}],
        "evidence_locations": [{"page": 9}, {"page": 1}],
        "origin_assertions": [
            {
                "observations": [
                    {
                        "page": 9,
                        "fact": {
                            "property_type": "document_quality",
                            "relation": "document_part_code",
                        },
                    },
                    {
                        "page": 1,
                        "fact": {
                            "property_type": "document_quality",
                            "relation": "document_part_reference",
                        },
                    },
                    {
                        "page": 12,
                        "fact": {
                            "property_type": "document_quality",
                            "relation": "document_part_code",
                        },
                    },
                ]
            }
        ],
    }
    assert finding_pages(finding) == {9, 12}
    assert finding_pages(
        {"page": 9, "document_context_basis_sources": [{"page": 1}]}
    ) == {9}


def test_duplicate_finding_id_does_not_silently_replace_evidence():
    """Не позволяет второй записи с тем же ID незаметно изменить предмет приёмки."""
    matrix, findings, decisions = sample()
    with pytest.raises(ValueError, match="Повтор finding_id"):
        evaluate(matrix, [*findings, {**findings[0], "page": 24}], decisions)


def test_basis_question_belongs_to_accepted_value_not_every_related_context_page():
    """План или расчёт-ссылка не превращаются в место претензии к принятому напору."""
    finding = {
        "page": 7,
        "evidence_locations": [{"page": 7}, {"page": 20}],
        "origin_assertions": [
            {
                "check": {
                    "rule": "design_basis_dependency",
                    "observations": [{"page": 7}, {"page": 20}],
                }
            },
            {
                "check": {
                    "rule": "design_basis_dependency",
                    "observations": [{"page": 9}, {"page": 20}],
                }
            },
        ],
    }
    assert finding_pages(finding) == {7, 9}


def test_external_project_and_normative_pages_are_not_locations_in_primary_pdf():
    """Лист другого тома из U не засчитывается как одноимённая страница проверяемого PDF."""
    finding = {
        "page": 8,
        "evidence_locations": [
            {"page": 12, "source_id": "D-p0012-f0001"},
            {"page": 19, "source_id": "U1"},
            {"page": 20, "source_id": "N-p0020-c1"},
            {"page": 21, "source_id": "T2"},
            {"page": 22, "source_id": "EQ-2"},
            {"page": 23, "source_kind": "U"},
        ],
    }
    assert finding_pages(finding) == {8, 12}
