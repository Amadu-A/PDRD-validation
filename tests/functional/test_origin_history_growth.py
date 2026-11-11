# tests/functional/test_origin_history_growth.py

"""Регрессии роста JSON при повторном объединении реальных инженерных кандидатов."""

import json
from copy import deepcopy

import pytest
from pdrd_analysis_service.application.engineering_program import (
    program_findings,
    verified_programmatic_check,
)
from pdrd_analysis_service.application.origin_history import compact_origin_history
from pdrd_analysis_service.application.use_cases.document_context import (
    DocumentPage,
    document_engineering_report,
    merge_cross_page_findings,
)
from pdrd_analysis_service.domain.engineering_assertion import read_assertion


def serialized(value):
    """Измеряет фактический JSON, а не число корневых элементов истории."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_history_keeps_unique_content_and_ordered_edges_without_nested_copies():
    """Все исходные поля доступны по проверяемой ссылке; разные статусы не сливаются."""
    first = {
        "origin": "vlm",
        "candidate_id": "raw-a",
        "page": 5,
        "comment": "Исходная претензия",
        "evidence": "Буквальная цитата",
        "status": "hypothesis",
        "source_ids": {"N": ["N1"], "T": ["T1"], "U": ["U1"], "D": ["D5"]},
        "visual_regions": [{"x_min": 20, "y_min": 30, "x_max": 40, "y_max": 50}],
    }
    second = {**first, "page": 7, "status": "needs_review"}
    origins = (
        first,
        second,
        {
            "origin": "merged_finding",
            "finding_id": "raw-a",
            "merged_into": "combined",
            "stage": "document_deduplication",
            "reason_code": "same_engineering_assertion",
            "origin_assertions": (first, second, first),
        },
    )
    original = deepcopy(origins)
    result = compact_origin_history(origins)
    history = next(item for item in result if item.get("origin") == "origin_history")
    merged = next(item for item in result if item.get("origin") == "merged_finding")
    references = merged["origin_assertions"]
    resolved = [history["records"][item["origin_ref"]] for item in references]
    assert resolved == [first, second, first]
    assert len(history["records"]) == 3
    assert result[:2] == (first, second)
    assert origins == original
    assert compact_origin_history(result) == result


@pytest.mark.parametrize("corruption", ["missing_reference", "changed_record"])
def test_broken_history_fails_explicitly_instead_of_losing_evidence(corruption):
    """Неполное происхождение нельзя выдавать за успешно сохранённый результат."""
    result = compact_origin_history(
        (
            {
                "origin": "merged_finding",
                "origin_assertions": [{"origin": "vlm", "evidence": "Исходный текст"}],
            },
        )
    )
    broken = deepcopy(result)
    if corruption == "missing_reference":
        broken[0]["origin_assertions"][0]["origin_ref"] = "absent"
    else:
        table = broken[-1]["records"]
        next(iter(table.values()))["evidence"] = "Подмена"
    with pytest.raises(ValueError):
        compact_origin_history(broken)


def footer_candidates(count=6):
    """Создаёт повтор одного реквизита на разных листах через действующий реестр."""
    code = "381/704-3-ИОС5.6"
    body = tuple(
        DocumentPage(
            page,
            code,
            "",
            (),
            (
                {
                    "text": code,
                    "block_no": 1,
                    "line_no": 1,
                    "word_no": 0,
                    "bbox": {"x_min": 540, "y_min": 910, "x_max": 760, "y_max": 930},
                },
            ),
        )
        for page in range(5, 5 + count)
    )
    pages = (DocumentPage(2, code + "-ТЧ Текстовая часть", "", ()), *body)
    return program_findings(
        document_engineering_report(pages, "history-growth-control")
    )


def test_repeated_real_merges_keep_all_pages_and_verification_with_bounded_json():
    """Двенадцать этапов не размножают историю в геометрической прогрессии."""
    drafts = footer_candidates()
    assert len(drafts) == 6
    current = drafts
    sizes = []
    for index in range(12):
        merged = merge_cross_page_findings(
            page_findings={item.page: (item,) for item in current},
            cross_findings=drafts,
            stage="regression-merge-" + str(index),
        )
        current = tuple(item for rows in merged.values() for item in rows)
        assert len(current) == 1
        finding = current[0]
        assert verified_programmatic_check(finding)
        assert read_assertion(finding.origin_assertions) is not None
        assert {s.page for s in finding.document_context_basis_sources} == {
            2,
            5,
            6,
            7,
            8,
            9,
            10,
        }
        assert {item["page"] for item in finding.evidence_locations} >= {
            5,
            6,
            7,
            8,
            9,
            10,
        }
        represented = {
            finding.finding_id,
            *(
                item.get("finding_id")
                for item in finding.origin_assertions
                if item.get("origin") == "merged_finding"
            ),
        }
        assert {item.finding_id for item in drafts} <= represented
        sizes.append(len(serialized(finding.origin_assertions).encode("utf-8")))
        assert sizes[-1] < 3_000_000
    assert sizes[-1] < sizes[0] * 20
