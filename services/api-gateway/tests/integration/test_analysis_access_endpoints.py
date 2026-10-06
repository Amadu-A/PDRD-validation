# services/api-gateway/tests/integration/test_analysis_access_endpoints.py

"""Проверяет защиту Analysis URL в настоящем FastAPI Gateway с имитацией Auth/User."""

import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.submit_analysis import SubmitAnalysis
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.core.settings import DatabaseSettings, Settings
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.infrastructure.analysis_scope import HttpUserAnalysisScopeChecker
from pdrd_api_gateway.main import create_app
from pdrd_api_gateway.transport.http.identity_authorization import IdentityAuthorizer
from pydantic import SecretStr


class Jobs:
    """Возвращает конкретные устойчивую job из памяти теста."""

    def __init__(self, jobs: tuple[AnalysisJob, ...]) -> None:
        """Индексирует задания по UUID."""
        self.jobs = {job.id: job for job in jobs}

    async def execute(self, *, job_id: UUID) -> AnalysisJob | None:
        """Имитирует чтение одной строки БД."""
        return self.jobs.get(job_id)


class Result:
    """Сигнализирует о достижении handler только после middleware."""

    async def execute(self, *, job_id: UUID) -> dict[str, object]:
        """Возвращает минимальный JSON результата."""
        return {"job_id": str(job_id), "findings": []}


class Submit:
    """Создаёт guest job через уже проверенный HTTP контракт."""

    def __init__(self, job: AnalysisJob) -> None:
        """Фиксирует заранее созданный job для ответа."""
        self.job = job

    async def execute(self, **options: object) -> AnalysisJob:
        """Проверяет, что guest access запрошен сервером, а не браузером."""
        assert options["guest_access"] is True
        assert options["owner_user_id"] is None
        return self.job


def test_middleware_binds_guest_owner_and_department(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Случайная ссылка, владелец и руководитель проходят разные ветки доступа."""
    owner_id = uuid4()
    head_id = uuid4()
    guest = AnalysisJob.create(document_id=uuid4(), guest_access=True)
    owned = AnalysisJob.create(document_id=uuid4(), owner_user_id=owner_id)
    token = guest.guest_access_token
    assert token is not None
    auth_calls = []
    scope_calls = []

    def auth_reply(request: httpx.Request) -> httpx.Response:
        """Разрешает только известные сессии с актуальным UUID и permission."""
        assert request.url.path == "/internal/v1/auth/introspect"
        assert request.headers["authorization"] == f"Bearer {'a' * 32}"
        auth_calls.append(request)
        session = json.loads(request.content)["token"]
        if session == "owner":
            return httpx.Response(
                200, json={"user_id": str(owner_id), "permissions": ["review.own.read"]}
            )
        if session == "head":
            return httpx.Response(
                200,
                json={
                    "user_id": str(head_id),
                    "permissions": ["review.scoped.read", "user_documents.scoped.read"],
                },
            )
        return httpx.Response(401)

    def scope_reply(request: httpx.Request) -> httpx.Response:
        """Имитирует защищённый ответ user-service для общего отдела."""
        assert request.url.path == "/internal/v1/access/review-scope"
        assert request.headers["authorization"] == f"Bearer {'u' * 32}"
        payload = json.loads(request.content)
        scope_calls.append(payload)
        return httpx.Response(
            200,
            json={
                "allowed": payload
                == {"actor_user_id": str(head_id), "owner_user_id": str(owner_id)}
            },
        )

    monkeypatch.setattr(
        IdentityAuthorizer,
        "_new_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(auth_reply)),
    )
    monkeypatch.setattr(
        HttpUserAnalysisScopeChecker,
        "_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(scope_reply)),
    )
    settings = Settings(
        _env_file=None,
        database=DatabaseSettings(password="test-only-password"),
        identity_proxy=IdentityProxySettings(
            enabled=True,
            authorization_enabled=True,
            auth_internal_key=SecretStr("a" * 32),
            user_service_internal_key=SecretStr("u" * 32),
            technical_assignment_access_key=SecretStr("t" * 32),
            trusted_proxy_key=SecretStr("p" * 32),
            public_origin="https://pdrd.example.test",
        ),
    )

    async def close() -> None:
        """Контейнер теста не открывает реальные соединения."""

    container = ApplicationContainer(
        settings=settings,
        check_readiness=None,
        shutdown_callback=close,
        get_analysis_job=Jobs((guest, owned)),
        get_analysis_result=Result(),
        submit_analysis=Submit(guest),
    )
    with TestClient(create_app(container)) as client:
        guest_result = client.get(
            f"/api/v1/analyses/{guest.id}/result",
            headers={"X-PDRD-Analysis-Access": token},
        )
        missing_token = client.get(f"/api/v1/analyses/{guest.id}/result")
        client.cookies.set("pdrd_session", "invalid")
        invalid_cookie = client.get(
            f"/api/v1/analyses/{guest.id}/result",
            headers={"X-PDRD-Analysis-Access": token},
        )
        client.cookies.set("pdrd_session", "head")
        foreign_review = client.get(f"/api/v1/analyses/{guest.id}/review")
        client.cookies.set("pdrd_session", "owner")
        owner_result = client.get(f"/api/v1/analyses/{owned.id}/result")
        client.cookies.set("pdrd_session", "head")
        head_result = client.get(f"/api/v1/analyses/{owned.id}/result")
        blocked_packages = client.get(
            f"/api/v1/normative/sections/{uuid4()}/user-packages/categories"
        )
        blocked_catalog = client.get("/api/v1/experience")
        client.cookies.clear()
        submit = client.post(
            "/api/v1/analyses",
            files={"pdf": ("drawing.pdf", b"fake-pdf", "application/pdf")},
            headers={"Origin": "https://pdrd.example.test"},
        )
        blocked_package_submit = client.post(
            "/api/v1/analyses",
            data={"user_package_document_ids": json.dumps([str(uuid4())])},
            files={"pdf": ("drawing.pdf", b"fake-pdf", "application/pdf")},
            headers={"Origin": "https://pdrd.example.test"},
        )
    assert guest_result.status_code == 200
    assert guest_result.headers["cache-control"] == "no-store"
    assert missing_token.status_code == 404
    assert invalid_cookie.status_code == 401
    assert foreign_review.status_code == 404
    assert owner_result.status_code == 200
    assert head_result.status_code == 200
    assert blocked_packages.status_code == 503
    assert blocked_catalog.status_code == 403
    assert blocked_package_submit.status_code == 401
    assert scope_calls == [
        {"actor_user_id": str(head_id), "owner_user_id": str(owner_id)}
    ]
    assert len(auth_calls) == 6
    assert submit.status_code == 202
    assert submit.json()["status_url"] == f"/api/v1/analyses/{guest.id}"
    assert submit.json()["access_token"] == token
    assert submit.json()["access_expires_at"] is not None


@pytest.mark.parametrize("signed_in", [False, True])
@pytest.mark.parametrize("package_field", [None, "[]"])
@pytest.mark.parametrize("file_kind", ["pdf", "cad"])
def test_plain_analysis_without_section_or_packages(
    monkeypatch: pytest.MonkeyPatch,
    signed_in: bool,
    package_field: str | None,
    file_kind: str,
) -> None:
    """PDF/CAD проходит с гостем и сессией, включая старый пустой список пакетов."""
    owner_id = uuid4()
    calls = []

    def auth_reply(request: httpx.Request) -> httpx.Response:
        """Возвращает действующего пользователя без административных прав."""
        return httpx.Response(200, json={"user_id": str(owner_id), "permissions": []})

    class PlainSubmit:
        """Проверяет параметры обычного анализа после настоящего middleware."""

        async def execute(self, **options: object) -> AnalysisJob:
            """Фиксирует владельца и отсутствие нормативного контекста."""
            calls.append(options)
            assert options["owner_user_id"] == (owner_id if signed_in else None)
            assert options["guest_access"] is (not signed_in)
            assert options["normative_section_id"] is None
            assert not options["user_package_document_ids"]
            return await SubmitAnalysis(Artifacts(), CreateJob()).execute(**options)

    class Artifacts:
        """Имитирует файловое хранилище обычного анализа без нормативного resolver."""

        async def save_request(self, **options: object) -> None:
            """Принимает реальные параметры PDF/CAD после прикладной проверки."""

        async def delete_request(self, **options: object) -> None:
            """Освобождает тестовые файлы при ошибке."""

    class CreateJob:
        """Имитирует сохранение задания с настоящим владельцем и пустым снимком."""

        async def execute(self, **options: object) -> AnalysisJob:
            """Проверяет отсутствие нормативного контекста перед созданием job."""
            assert options["normative_snapshot"] is None
            return AnalysisJob.create(**options)

    async def close() -> None:
        """Завершает контейнер без внешних подключений."""

    monkeypatch.setattr(
        IdentityAuthorizer,
        "_new_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(auth_reply)),
    )
    settings = Settings(
        _env_file=None,
        database=DatabaseSettings(password="test-only-password"),
        identity_proxy=IdentityProxySettings(
            enabled=True,
            authorization_enabled=True,
            auth_internal_key=SecretStr("a" * 32),
            user_service_internal_key=SecretStr("u" * 32),
            technical_assignment_access_key=SecretStr("t" * 32),
            trusted_proxy_key=SecretStr("p" * 32),
            public_origin="https://pdrd.example.test",
        ),
    )
    container = ApplicationContainer(
        settings=settings,
        check_readiness=None,
        shutdown_callback=close,
        submit_analysis=PlainSubmit(),
    )
    with TestClient(create_app(container)) as client:
        if signed_in:
            client.cookies.set("pdrd_session", "owner")
        response = client.post(
            "/api/v1/analyses",
            files={
                file_kind: (
                    "drawing.pdf" if file_kind == "pdf" else "drawing.dxf",
                    b"drawing",
                    "application/octet-stream",
                )
            },
            data={}
            if package_field is None
            else {"user_package_document_ids": package_field},
            headers={"Origin": "https://pdrd.example.test"},
        )
    assert response.status_code == 202, response.text
    assert len(calls) == 1
