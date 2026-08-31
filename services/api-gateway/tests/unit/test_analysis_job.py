# services/api-gateway/tests/unit/test_analysis_job.py

"""Unit-тесты lifecycle задания анализа."""

import pytest
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    InvalidAnalysisJobTransitionError,
)


def test_new_analysis_job_is_pending() -> None:
    """Проверяет начальное состояние задания."""
    job = AnalysisJob.create()

    assert job.status is AnalysisJobStatus.PENDING
    assert job.attempt_count == 0
    assert job.error_code is None
    assert job.error_message is None


def test_analysis_job_successful_lifecycle() -> None:
    """Проверяет штатный lifecycle задания."""
    job = AnalysisJob.create()

    job.mark_queued()
    assert job.status is AnalysisJobStatus.QUEUED

    job.mark_processing()
    assert job.status is AnalysisJobStatus.PROCESSING
    assert job.attempt_count == 1

    job.mark_completed()
    assert job.status is AnalysisJobStatus.COMPLETED


def test_analysis_job_failure_lifecycle() -> None:
    """Проверяет сохранение информации об ошибке."""
    job = AnalysisJob.create()

    job.mark_queued()
    job.mark_processing()

    job.mark_failed(
        error_code="analysis_error",
        error_message="Ошибка выполнения анализа.",
    )

    assert job.status is AnalysisJobStatus.FAILED
    assert job.error_code == "analysis_error"
    assert job.error_message == "Ошибка выполнения анализа."


def test_terminal_job_cannot_be_restarted() -> None:
    """Проверяет запрет возврата terminal job в processing."""
    job = AnalysisJob.create()

    job.mark_queued()
    job.mark_processing()
    job.mark_completed()

    with pytest.raises(
        InvalidAnalysisJobTransitionError,
    ):
        job.mark_processing()
