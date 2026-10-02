"""Unit-тесты владельца и ограниченной гостевой ссылки анализа."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pdrd_api_gateway.domain.analysis_access import can_access_analysis_job
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.transport.http.analysis_access import analysis_job_route


def test_guest_link_is_random_scoped_and_expires() -> None:
    """UUID задания не заменяет отдельный ограниченный гостевой ключ."""
    job = AnalysisJob.create(guest_access=True)
    token = job.guest_access_token
    assert token is not None and len(token) >= 40
    assert token != job.guest_access_token_hash
    assert job.guest_access_expires_at is not None

    now = datetime.now(UTC)
    assert can_access_analysis_job(
        job, actor_user_id=None, guest_access_token=token, now=now
    )
    assert not can_access_analysis_job(
        job, actor_user_id=None, guest_access_token=None, now=now
    )
    assert not can_access_analysis_job(
        job, actor_user_id=None, guest_access_token="another-token", now=now
    )
    assert not can_access_analysis_job(
        job, actor_user_id=None, guest_access_token=token, now=now, review=True
    )
    assert not can_access_analysis_job(
        replace(job, guest_access_expires_at=now - timedelta(seconds=1)),
        actor_user_id=None,
        guest_access_token=token,
        now=now,
    )


def test_owner_and_legacy_job_access() -> None:
    """Владелец видит свой job; чужой, guest и legacy job закрыты."""
    owner_id = uuid4()
    other_id = uuid4()
    now = datetime.now(UTC)
    job = AnalysisJob.create(owner_user_id=owner_id)

    assert can_access_analysis_job(
        job, actor_user_id=owner_id, guest_access_token=None, now=now, review=True
    )
    assert not can_access_analysis_job(
        job, actor_user_id=other_id, guest_access_token=None, now=now
    )
    assert not can_access_analysis_job(
        job,
        actor_user_id=None,
        guest_access_token=None,
        now=now,
        scoped_authorized=True,
    )
    assert can_access_analysis_job(
        job,
        actor_user_id=other_id,
        guest_access_token=None,
        now=now,
        scoped_authorized=True,
    )
    assert not can_access_analysis_job(
        AnalysisJob.create(), actor_user_id=owner_id, guest_access_token=None, now=now
    )


def test_job_routes_include_review_and_capture() -> None:
    """Все пути с артефактами и Review имеют единый job ID."""
    job_id = uuid4()
    assert analysis_job_route(f"/api/v1/analyses/{job_id}/result") == (job_id, False)
    assert analysis_job_route(f"/api/v1/analyses/{job_id}/review/commands") == (
        job_id,
        True,
    )
    assert analysis_job_route(f"/api/v1/analyses/{job_id}/reviewed-pdf") == (
        job_id,
        True,
    )
    assert analysis_job_route(f"/api/v1/experience/capture/{job_id}") == (job_id, True)
    assert analysis_job_route("/api/v1/analyses/project-context/preflight") is None
