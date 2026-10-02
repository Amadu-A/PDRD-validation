"""HTTP-проверки доступа к результату и Review по durable job binding."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pdrd_api_gateway.application.ports.analysis_scope import AnalysisScopeUnavailable
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.transport.http.analysis_access import enforce_analysis_job_access
from sqlalchemy.exc import SQLAlchemyError


class Jobs:
    """Минимальный reader без обхода HTTP middleware."""

    def __init__(self, job: AnalysisJob | None, *, failed: bool = False) -> None:
        """Сохраняет нужный job и режим недоступной БД."""
        self.job = job
        self.failed = failed

    async def execute(self, *, job_id):
        """Возвращает только заданный job либо имитирует недоступную БД."""
        if self.failed:
            raise SQLAlchemyError("private db connection detail")
        return self.job if self.job is not None and self.job.id == job_id else None


class Scope:
    """Имитирует решение каталога о текущем отделе пользователя."""

    def __init__(self, allowed: bool | None) -> None:
        """None означает временную недоступность сервиса."""
        self.allowed = allowed

    async def allows(self, *, actor_user_id, owner_user_id):
        """Возвращает решение и требует оба внутренних UUID."""
        assert actor_user_id is not None and owner_user_id is not None
        if self.allowed is None:
            raise AnalysisScopeUnavailable
        return self.allowed


def browser(
    job: AnalysisJob | None, *, actor=None, failed=False, scope=None
) -> TestClient:
    """Собирает HTTP контур без запуска инфраструктуры."""
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=SimpleNamespace(
            identity_proxy=SimpleNamespace(public_origin="https://pdrd.example")
        )
    )
    jobs = Jobs(job, failed=failed)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def guarded(request: Request, path: str):
        """Пропускает запрос к handler только после durable проверки."""
        del path
        denial = await enforce_analysis_job_access(
            request, jobs, actor, scope_checker=scope
        )
        return denial or JSONResponse({"status": "ok"})

    return TestClient(app)


def test_guest_can_read_result_but_cannot_open_review() -> None:
    """Гостевой токен открывает результат и PDF только до истечения TTL."""
    job = AnalysisJob.create(guest_access=True)
    token = job.guest_access_token
    assert token is not None
    client = browser(job)
    base = f"/api/v1/analyses/{job.id}"

    assert (
        client.get(f"{base}/result", params={"access_token": token}).status_code == 200
    )
    assert (
        client.get(f"{base}/annotated-pdf", params={"access_token": token}).status_code
        == 200
    )
    assert client.get(f"{base}/result").status_code == 404
    assert (
        client.get(f"{base}/review", params={"access_token": token}).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/experience/capture/{job.id}",
            params={"access_token": token},
            headers={"Origin": "https://pdrd.example"},
        ).status_code
        == 404
    )


def test_expired_guest_link_and_other_owner_are_denied() -> None:
    """Прошедший срок и чужая учётная запись закрывают тот же URL."""
    job = AnalysisJob.create(guest_access=True)
    token = job.guest_access_token
    assert token is not None
    expired = replace(
        job, guest_access_expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    assert (
        browser(expired)
        .get(f"/api/v1/analyses/{job.id}/result", params={"access_token": token})
        .status_code
        == 404
    )

    owner_id = uuid4()
    owned = AnalysisJob.create(owner_user_id=owner_id)
    endpoint = f"/api/v1/analyses/{owned.id}/result"
    assert browser(owned, actor=owner_id).get(endpoint).status_code == 200
    assert browser(owned, actor=uuid4()).get(endpoint).status_code == 404


def test_mutation_requires_origin_and_database_errors_hide_details() -> None:
    """Случайная ссылка не обходит Origin, а DB error не раскрывает адрес БД."""
    job = AnalysisJob.create(guest_access=True)
    token = job.guest_access_token
    assert token is not None
    endpoint = f"/api/v1/analyses/{job.id}/cancel"
    client = browser(job)
    assert (
        client.post(endpoint, headers={"X-PDRD-Analysis-Access": token}).status_code
        == 403
    )
    assert (
        client.post(
            endpoint,
            headers={"X-PDRD-Analysis-Access": token, "Origin": "https://pdrd.example"},
        ).status_code
        == 200
    )

    failed = browser(job, failed=True).get(
        f"/api/v1/analyses/{job.id}/result", params={"access_token": token}
    )
    assert failed.status_code == 503
    assert "private db connection detail" not in failed.text


def test_other_user_requires_verified_department_scope() -> None:
    """Руководитель проходит только с актуальным решением user-service."""
    owner = uuid4()
    actor = uuid4()
    job = AnalysisJob.create(owner_user_id=owner)
    endpoint = f"/api/v1/analyses/{job.id}/review"
    assert browser(job, actor=actor, scope=Scope(True)).get(endpoint).status_code == 200
    assert (
        browser(job, actor=actor, scope=Scope(False)).get(endpoint).status_code == 404
    )
    assert browser(job, actor=actor, scope=Scope(None)).get(endpoint).status_code == 503
