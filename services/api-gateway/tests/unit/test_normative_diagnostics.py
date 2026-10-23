# services/api-gateway/tests/unit/test_normative_diagnostics.py

"""Компактная статистика не удваивает общие операции и не выдаёт отсутствие данных за ноль."""

import json
from uuid import uuid4

from pdrd_api_gateway.application.normative_diagnostics import normative_diagnostics
from pdrd_api_gateway.transport.http.analysis_access import analysis_job_route


def test_diagnostic_counts_actual_operations_and_requirement_outcomes_once():
    """Повторные представления одной операции и requirement ID считаются один раз."""
    metric = {
        "operation_id": "op",
        "physical_requests": 3,
        "http_503": 2,
        "retries": 2,
        "wait_ms": 10,
        "batch_sizes": [4, 4, 4],
        "stage": "requirements",
    }
    record = {
        "requirement_id": "req",
        "source_kind": "N",
        "result": "violated",
        "rule": "numeric_bound",
        "reason_code": "applicable_requirement_violated",
        "applicability": "applicable",
        "currency": "current_mandatory",
    }
    page = {
        "page_number": 1,
        "normative_queries": ["расход  10", "напор 10"],
        "normative_query_counts": {"base": 1, "additional": 1, "unique": 2},
        "normative_sources": [{"text": "confidential", "source_id": "N1"}],
        "normative_check": {"metrics": {"requirement_plan": {"checks": [record]}}},
    }
    result = {
        "pages": [page, {**page, "page_number": 2}],
        "retrieval_operations": [
            {"retrieval_metrics": metric},
            {"retrieval_metrics": metric},
        ],
        "findings": [
            {
                "status": "confirmed",
                "origin_assertions": [
                    {"check": {"calculation": {"requirement_id": "req"}}}
                ],
            }
        ],
    }
    summary = normative_diagnostics(result, job_id="job")
    assert summary["pages"] == 2 and summary["requirements"] == 1
    assert summary["embedding"]["physical_requests"] == 3 and summary["embedding"][
        "batch_sizes"
    ] == {"4": 3}
    assert summary["queries"]["unique_document_queries"] == 2
    assert summary["normative_candidates"] == {
        "created": 1,
        "retained": 1,
        "not_retained": 0,
    }
    assert "confidential" not in json.dumps(summary)


def test_old_result_without_embedding_telemetry_does_not_claim_zero_requests():
    """Неизвестную нагрузку старого прогона нельзя подставить в сравнение производительности."""
    summary = normative_diagnostics({"pages": [{"page_number": 1}]}, job_id="job")
    assert not summary["telemetry_available"] and summary["embedding"] is None
    assert not summary["requirement_plan_available"]


def test_same_requirement_on_different_project_values_keeps_both_outcomes():
    """Повторный поиск нормы не затирает проверку другого проектного значения на другой странице."""
    records = [
        {
            "requirement_id": "same",
            "result": state,
            "observations": [{"page": page, "value": value}],
        }
        for page, value, state in [(1, "4", "violated"), (2, "6", "satisfied")]
    ]
    result = {
        "pages": [
            {"normative_check": {"metrics": {"requirement_plan": {"checks": records}}}}
        ]
    }
    summary = normative_diagnostics(result, job_id="job")
    assert summary["requirements"] == 2 and summary["unique_requirements"] == 1
    assert summary["requirement_plan"]["result"] == {"violated": 1, "satisfied": 1}


def test_compact_diagnostics_uses_existing_analysis_owner_acl():
    """Новый read-only маршрут остаётся в общей проверке владельца, в том числе для гостя."""
    job_id = uuid4()
    assert analysis_job_route(f"/api/v1/analyses/{job_id}/diagnostics/normative") == (
        job_id,
        False,
    )


def test_unavailable_persistent_protocol_does_not_become_zero_checks():
    """Компактная ссылка без восстановления явно отличается от настоящего пустого плана."""
    result = {
        "metrics": {
            "independent_normative": {
                "requirement_plan": {
                    "check_count": 10412,
                    "protocol": {"protocol_id": "test"},
                }
            }
        }
    }
    diagnostic = normative_diagnostics(result, job_id="job")
    assert diagnostic["requirements"] is None
    assert diagnostic["normative_candidates"] is None
    assert diagnostic["requirement_plan_state"] == "protocol_unavailable"
