# services/api-gateway/tests/unit/test_performance_report.py

"""Сводка задания: интервалы параллельных страниц и отсутствие вымышленных измерений."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from pdrd_api_gateway.application.performance import performance_report


def test_report_binds_actual_job_and_preserves_unknown_usage():
    """Перекрывающиеся страницы не удваивают wall; чужой document_id не попадает в отчёт."""
    start = datetime(2026, 11, 13, tzinfo=UTC)
    job = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        created_at=start,
        updated_at=start + timedelta(seconds=3),
        source_artifacts_deleted_at=None,
    )
    pages = []
    for number in range(1, 4):
        profile = {
            "document_id": str(job.document_id) if number < 3 else "foreign",
            "stage": "check_norms",
            "page": number,
            "started_at": start.isoformat(),
            "ended_at": (start + timedelta(seconds=1)).isoformat(),
            "logical_vlm_calls": 1,
            "physical_vlm_calls": 1,
            "retries": 0,
            "provider_work_ms": 600,
            "input_tokens": None,
            "output_tokens": 5,
            "gpu_wait_ms": 10,
            "cache_hits": 0,
            "cache_misses": 1,
            "context_bytes": 100,
            "image_bytes": 20,
        }
        pages.append({"normative_check": {"metrics": {"execution_profile": profile}}})
    result = {"pages": pages}
    report = performance_report(job=job, result=result)
    assert report["job_id"] == str(job.id) and report["total_wall_ms"] == 3000
    stage = report["stages"][0]
    assert stage["wall_ms"] == 1000 and stage["pages"] == 2
    assert stage["input_tokens"] is None and stage["output_tokens"] == 10
    assert stage["retries"] == 0 and stage["provider_work_ms"] == 1200
    assert report["rejected_profiles"] == 1
    # Retention меняет updated_at; это уже не время завершения анализа.
    job.source_artifacts_deleted_at = job.updated_at
    assert performance_report(job=job, result=result)["total_wall_ms"] is None
    assert performance_report(job=job, result={"pages": []}) is None
