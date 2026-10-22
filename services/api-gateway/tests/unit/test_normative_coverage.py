# services/api-gateway/tests/unit/test_normative_coverage.py

"""Неполная нормативная проверка остаётся явной в API завершённого задания."""

import pytest
from pdrd_api_gateway.application.normative_coverage import normative_coverage


@pytest.mark.parametrize(
    "complete,expected", [(True, "complete"), (False, "incomplete")]
)
def test_independent_coverage_is_projected_without_changing_findings(
    complete, expected
):
    """Читает сохранённые метрики, не запускает построение каталога и не выводит успех из нуля findings."""
    result = {
        "pages": [
            {
                "normative_check": {
                    "metrics": {
                        "independent_normative": {
                            "requirement_plan": {
                                "coverage": {
                                    "source_complete": complete,
                                    "corpus_requirements": 20,
                                },
                                "generations": [{"generation_id": "gen"}],
                                "unavailable_documents": []
                                if complete
                                else [{"document_id": "doc", "reason": "not_indexed"}],
                            }
                        }
                    }
                }
            }
        ],
        "findings": [],
    }
    report = normative_coverage(result)
    assert report["normative_coverage"] == expected
    assert report["normative_atomic_check"] == (
        "available" if complete else "unavailable"
    )
    assert report["corpus_requirements"] == 20 and result["findings"] == []


def test_old_workflow_missing_scope_is_incomplete_and_unknown_old_result_is_not_guessed():
    """Отсутствие новых метрик не выдаётся за измеренный полный охват."""
    assert normative_coverage({"findings": []}) is None
    report = normative_coverage(
        {
            "pages": [
                {
                    "metrics": {
                        "independent_normative": {
                            "mode": "corpus_scope_missing",
                            "source_complete": False,
                        }
                    }
                }
            ]
        }
    )
    assert report["normative_coverage"] == "incomplete"
    assert report["normative_atomic_check"] == "unavailable"
