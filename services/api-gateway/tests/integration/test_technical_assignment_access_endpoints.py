"""Проверяет HMAC-доступ к preflight ТЗ в настоящем FastAPI Gateway."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.identity_proxy_settings import IdentityProxySettings
from pdrd_api_gateway.core.settings import DatabaseSettings, Settings
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.main import create_app
from pydantic import SecretStr


@dataclass
class IndexState:
    """Минимальный ответ индексации ТЗ."""

    technical_assignment_id: UUID
    index_status: str = "ready"
    index_error: str | None = None


class Index:
    """Хранит подготовленный UUID только в памяти HTTP-теста."""

    def __init__(self) -> None:
        """Задание ещё не подготовлено."""
        self.technical_assignment_id = None

    async def register(self, *, snapshot, content):
        """Имитирует регистрацию без внешнего Knowledge Service."""
        assert content == b"test-assignment"
        self.technical_assignment_id = snapshot.technical_assignment_id
        return IndexState(snapshot.technical_assignment_id)

    async def get_status(self, *, technical_assignment_id):
        """Возвращает статус по точному подготовленному UUID."""
        assert technical_assignment_id == self.technical_assignment_id
        return IndexState(technical_assignment_id)


class Content:
    """Имитирует хранение исходного ТЗ."""

    async def get_content(self, *, technical_assignment_id):
        """Возвращает bytes только после HTTP-проверки ключа."""
        assert isinstance(technical_assignment_id, UUID)
        return type(
            "Document", (), {"content": b"%PDF-fake", "mime_type": "application/pdf"}
        )()


class Submit:
    """Фиксирует достижение analysis use case после HMAC-проверки."""

    def __init__(self) -> None:
        """Счётчик вызовов нужен для проверки отказа до записи job."""
        self.calls = 0

    async def execute(self, **options):
        """Имитирует принятие полного multipart анализа."""
        self.calls += 1
        assert options["guest_access"] is True
        return AnalysisJob.create(document_id=uuid4(), guest_access=True)


def browser(*, enabled: bool, index: Index, submit: Submit) -> TestClient:
    """Собирает Gateway с отключёнными внешними сетевыми соединениями."""
    settings = Settings(
        _env_file=None,
        database=DatabaseSettings(password="test-only-password"),
        identity_proxy=IdentityProxySettings(
            enabled=enabled,
            authorization_enabled=enabled,
            auth_internal_key=SecretStr("a" * 32),
            user_service_internal_key=SecretStr("u" * 32),
            technical_assignment_access_key=SecretStr("t" * 32),
            trusted_proxy_key=SecretStr("p" * 32),
            public_origin="https://pdrd.example.test",
        ),
    )

    async def close() -> None:
        """Контейнер теста ничего не закрывает."""

    container = ApplicationContainer(
        settings=settings,
        check_readiness=None,
        shutdown_callback=close,
        technical_assignment_index_coordinator=index,
        technical_assignment_content_reader=Content(),
        submit_analysis=submit,
    )
    return TestClient(create_app(container))


def test_auth_mode_requires_capability_for_status_content_and_reuse() -> None:
    """Чужой UUID не выдаёт ТЗ и не переносится в analysis без HMAC."""
    index = Index()
    submit = Submit()
    with browser(enabled=True, index=index, submit=submit) as client:
        rejected_origin = client.post(
            "/api/v1/normative/technical-assignments/prepare",
            files={"file": ("brief.pdf", b"test-assignment", "application/pdf")},
            data={"section_id": str(uuid4())},
            headers={"Origin": "https://other.example.test"},
        )
        assert rejected_origin.status_code == 403
        assert index.technical_assignment_id is None
        prepared = client.post(
            "/api/v1/normative/technical-assignments/prepare",
            files={"file": ("brief.pdf", b"test-assignment", "application/pdf")},
            data={"section_id": str(uuid4())},
            headers={"Origin": "https://pdrd.example.test"},
        )
        assert prepared.status_code == 202
        payload = prepared.json()
        token = payload["access_token"]
        assert token.startswith("v1.")
        assert payload["access_expires_at"] is not None
        assert prepared.headers["cache-control"] == "no-store"
        base = f"/api/v1/normative/technical-assignments/{payload['technical_assignment_id']}"

        assert client.get(f"{base}/status").status_code == 404
        assert client.get(f"{base}/content").status_code == 404
        status = client.get(
            f"{base}/status", headers={"X-PDRD-Technical-Assignment-Access": token}
        )
        content = client.get(
            f"{base}/content", headers={"X-PDRD-Technical-Assignment-Access": token}
        )
        assert status.status_code == 200
        assert status.headers["cache-control"] == "no-store"
        assert content.status_code == 200 and content.content == b"%PDF-fake"
        assert content.headers["cache-control"] == "no-store"

        form = {
            "technical_assignment_id": payload["technical_assignment_id"],
            "technical_assignment_analysis_document_id": payload[
                "analysis_document_id"
            ],
        }
        files = {
            "pdf": ("drawing.pdf", b"fake-pdf", "application/pdf"),
            "technical_assignment": (
                "brief.pdf",
                b"test-assignment",
                "application/pdf",
            ),
        }
        denied = client.post(
            "/api/v1/analyses",
            data=form,
            files=files,
            headers={"Origin": "https://pdrd.example.test"},
        )
        allowed = client.post(
            "/api/v1/analyses",
            data={**form, "technical_assignment_access_token": token},
            files=files,
            headers={"Origin": "https://pdrd.example.test"},
        )
    assert denied.status_code == 404
    assert allowed.status_code == 202
    assert submit.calls == 1


def test_legacy_mode_keeps_preflight_contract_without_capability() -> None:
    """Отключённая авторизация не меняет прежний запуск и чтение ТЗ."""
    index = Index()
    with browser(enabled=False, index=index, submit=Submit()) as client:
        prepared = client.post(
            "/api/v1/normative/technical-assignments/prepare",
            files={"file": ("brief.pdf", b"test-assignment", "application/pdf")},
            data={"section_id": str(uuid4())},
        )
        assert prepared.status_code == 202
        payload = prepared.json()
        status = client.get(
            f"/api/v1/normative/technical-assignments/{payload['technical_assignment_id']}/status"
        )
    assert payload["access_token"] is None
    assert payload["access_expires_at"] is None
    assert status.status_code == 200
