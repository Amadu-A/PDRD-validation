# tests/functional/test_review_api.py

"""Сквозные HTTP-сценарии Gateway → Experience → доверенный источник Gateway.

Исполняются настоящие маршруты, схемы, use cases, проверка оригинала и домен.
Только сеть, исходные артефакты и PostgreSQL заменены тестовыми адаптерами.
Реальное SQL-хранилище проверяется отдельным изолированным прогоном.
"""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pdrd_api_gateway.application.use_cases.get_review_source import GetReviewSource
from pdrd_api_gateway.application.use_cases.manage_review import (
    CompletedJobReviewAccess,
    ManageReview,
)
from pdrd_api_gateway.core.container import ApplicationContainer as GatewayContainer
from pdrd_api_gateway.core.settings import Settings as GatewaySettings
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus
from pdrd_api_gateway.infrastructure.review import (
    ControlledReviewContext,
    HttpReviewService,
)
from pdrd_api_gateway.main import create_app as gateway_app
from pdrd_experience_service.application.use_cases.review import (
    ChangeReview,
    OpenReview,
)
from pdrd_experience_service.core.container import (
    ApplicationContainer as ExperienceContainer,
)
from pdrd_experience_service.core.settings import Settings as ExperienceSettings
from pdrd_experience_service.domain.review import ReviewConflictError
from pdrd_experience_service.infrastructure.analysis.completed_reader import (
    VerifiedCompletedAnalysisReader,
)
from pdrd_experience_service.infrastructure.analysis.http_source import (
    GatewayAnalysisSource,
)
from pdrd_experience_service.main import create_app as experience_app

UI_KEY = "test-review-ui-channel-00000000000000"
INTERNAL_KEY = "test-review-internal-channel-00000000"
BOX = {"x_min": 10, "y_min": 20, "x_max": 120, "y_max": 140}
CARD = {"x_min": 300, "y_min": 30, "x_max": 700, "y_max": 200}


class MemoryReviews:
    """Атомарный порт Review для проверки HTTP без рабочего PostgreSQL."""

    def __init__(self) -> None:
        """Создаёт пустое изолированное хранилище."""
        self.sessions = {}

    async def load(self, job_id):
        """Возвращает неизменяемый доменный снимок."""
        return self.sessions.get(job_id)

    async def insert(self, session):
        """Не допускает замены оригинала при повторном открытии."""
        if session.job_id in self.sessions:
            raise ReviewConflictError("Duplicate.")
        self.sessions[session.job_id] = session

    async def update(self, session, *, expected_revision):
        """Отклоняет запись из устаревшей вкладки."""
        if self.sessions[session.job_id].revision != expected_revision:
            raise ReviewConflictError("Stale.")
        self.sessions[session.job_id] = session


class Jobs:
    """Источник состояния единственного серверного задания."""

    def __init__(self, job_id, document_id):
        """Готовит завершённое задание, не используя JSON браузера."""
        self.job_id = job_id
        self.job = SimpleNamespace(
            document_id=document_id, status=AnalysisJobStatus.COMPLETED
        )

    async def execute(self, *, job_id):
        """Неизвестные UUID не имеют доступа к существующему Review."""
        return self.job if job_id == self.job_id else None


class Artifacts:
    """Источник оригинального PDF и результата текущего документа."""

    def __init__(self, document_id):
        """Готовит метаданные исходника и полное нормативное основание."""
        self.document_id = document_id

    async def load_request(self, *, document_id):
        """Возвращает исходник только ожидаемого документа."""
        assert document_id == self.document_id
        return SimpleNamespace(
            submission=SimpleNamespace(
                document_id=document_id, pdf_file_name="План.pdf"
            ),
            pdf_content=b"%PDF-1.7\nserver-original",
        )

    async def load_result(self, *, document_id):
        """Возвращает located, unlocated и гипотезу для проверки отбора."""
        assert document_id == self.document_id
        return {
            "findings": [
                {
                    "finding_id": "vlm:1",
                    "page": 1,
                    "comment": "Исходный полный текст VLM",
                    "basis": "СП 1, п. 2",
                },
                {
                    "finding_id": "vlm:2",
                    "page": 1,
                    "comment": "Замечание без координат",
                },
                {
                    "finding_id": "hypothesis:1",
                    "page": 1,
                    "comment": "Гипотеза",
                    "status": "hypothesis",
                },
            ]
        }


class Visualizations:
    """Сохранённая серверная геометрия; браузер не передаёт её при открытии."""

    def __init__(self, job_id, document_id):
        """Привязывает лист и предложение области к одному заданию."""
        self.job_id, self.document_id = job_id, document_id

    async def execute(self, *, job_id):
        """Возвращает данные действующего контракта визуализации."""
        assert job_id == self.job_id
        return {
            "job_id": str(job_id),
            "document_id": str(self.document_id),
            "pages": [
                {
                    "page_number": 1,
                    "image_base64": "not-copied-to-review",
                    "locations": [
                        {
                            "finding_id": "vlm:1",
                            "status": "located",
                            "method": "vlm",
                            "regions": [
                                {"bbox": BOX, "source": "vlm", "confidence": 0.9}
                            ],
                        }
                    ],
                }
            ],
        }


class ServiceTransport(httpx.AsyncBaseTransport):
    """Соединяет настоящие ASGI-приложения без внешних TCP-портов."""

    def __init__(self):
        """Инициализирует registry, чтобы зависимости могли ссылаться друг на друга."""
        self.apps = {}

    async def handle_async_request(self, request):
        """Маршрутизирует серверный HTTP по DNS имени сервиса."""
        return await httpx.ASGITransport(
            app=self.apps[request.url.host]
        ).handle_async_request(request)


async def shutdown():
    """Тестовые зависимости не требуют освобождения сетевых ресурсов."""


@pytest.fixture
def flow():
    """Собирает оба сервиса и действующие адаптеры в одну изолированную систему."""
    job_id, document_id = uuid4(), uuid4()
    jobs, artifacts = Jobs(job_id, document_id), Artifacts(document_id)
    network = ServiceTransport()
    reviews = MemoryReviews()
    gateway = GatewayContainer(
        settings=GatewaySettings(
            _env_file=None,
            environment="test",
            review={
                "enabled": True,
                "controlled_access": True,
                "actor": "engineer:test",
                "ui_key": UI_KEY,
                "internal_key": INTERNAL_KEY,
            },
        ),
        check_readiness=None,
        shutdown_callback=shutdown,
        manage_review=ManageReview(
            contexts=ControlledReviewContext("engineer:test"),
            access=CompletedJobReviewAccess(jobs),
            service=HttpReviewService(
                "http://experience", INTERNAL_KEY, transport=network
            ),
        ),
        get_review_source=GetReviewSource(
            jobs, artifacts, Visualizations(job_id, document_id)
        ),
    )
    experience = ExperienceContainer(
        settings=ExperienceSettings(
            _env_file=None,
            enabled=True,
            review_api_enabled=True,
            internal_key=INTERNAL_KEY,
            database={"password": "test-only-password"},
        ),
        check_readiness=None,
        shutdown_callback=shutdown,
        reviews=reviews,
        open_review=OpenReview(
            analyses=VerifiedCompletedAnalysisReader(
                GatewayAnalysisSource("http://gateway", INTERNAL_KEY, transport=network)
            ),
            repository=reviews,
        ),
        change_review=ChangeReview(reviews),
    )
    network.apps = {
        "gateway": gateway_app(gateway),
        "experience": experience_app(experience),
    }
    return SimpleNamespace(
        job_id=job_id,
        jobs=jobs,
        reviews=reviews,
        network=network,
        gateway=gateway,
        experience=experience,
        endpoint=f"/api/v1/analyses/{job_id}/review",
    )


def client(flow, *, base_url="http://review.test"):
    """Клиент серверного фронта с реальным Origin; ключ добавляет серверный nginx."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(flow.network.apps["gateway"]),
        base_url=base_url,
        headers={"X-PDRD-Review-Key": UI_KEY, "Origin": base_url},
    )


async def command(browser, flow, revision, **body):
    """Отправляет строгую команду с явной ожидаемой ревизией."""
    return await browser.post(
        flow.endpoint + "/commands", json={"expected_revision": revision, **body}
    )


async def test_restore_edit_reject_undo_and_gold_through_both_services(flow):
    """Полные оригиналы, основания, решения и геометрия переживают повторное чтение."""
    manual_id = f"manual:{uuid4()}"
    async with client(flow) as browser:
        initial = await browser.post(flow.endpoint + "/open")
        assert initial.status_code == 200, initial.text
        rows = initial.json()["findings"]
        assert len(rows) == 2
        assert rows[0]["original_basis"] == "СП 1, п. 2"
        assert rows[0]["issue_box"] is None
        assert rows[1]["proposed_regions"] == []
        edited = await command(
            browser,
            flow,
            0,
            action="edit",
            finding_id="vlm:1",
            text="Исправленный полный текст",
            normative_basis="СП 1, п. 3",
        )
        assert edited.status_code == 200
        rejected = await command(
            browser, flow, 1, action="decide", finding_id="vlm:1", decision="rejected"
        )
        assert rejected.json()["findings"][0]["experience_tag"] == "edited"
        assert (
            rejected.json()["findings"][0]["original_text"]
            == "Исходный полный текст VLM"
        )
        reset = await command(browser, flow, 2, action="reset", finding_id="vlm:1")
        assert reset.json()["findings"][0]["decision"] == "pending"
        added = await command(
            browser,
            flow,
            3,
            action="add",
            finding_id=manual_id,
            page_number=1,
            text="Пропущенная ошибка",
            normative_basis="СП 2",
            issue_box=BOX,
            callout_box=CARD,
        )
        assert added.status_code == 200, added.text
        accepted = await command(
            browser, flow, 4, action="decide", finding_id=manual_id, decision="accepted"
        )
        assert accepted.json()["findings"][-1]["experience_tag"] == "gold"
        geometry = await command(
            browser,
            flow,
            5,
            action="geometry",
            finding_id=manual_id,
            regions=[{**BOX, "x_max": 150}],
            callout_box=CARD,
        )
        assert geometry.json()["findings"][-1]["decision"] == "pending"
        restored = await browser.post(flow.endpoint + "/open")
        read = await browser.get(flow.endpoint)
        assert read.json() == restored.json()
        assert read.json()["revision"] == 6
        assert read.json()["findings"][-1]["issue_box"]["x_max"] == 150
    session = await flow.reviews.load(flow.job_id)
    assert len(session.history) == 7
    assert all(event.actor == "engineer:test" for event in session.history)


@pytest.mark.parametrize(
    "body",
    [
        {"action": "decide", "finding_id": "vlm:1", "decision": "pending"},
        {
            "action": "decide",
            "finding_id": "vlm:1",
            "decision": "accepted",
            "actor": "attacker",
        },
        {
            "action": "edit",
            "finding_id": "vlm:1",
            "text": "x",
            "normative_basis": "",
            "original_text": "forged",
        },
        {
            "action": "geometry",
            "finding_id": "vlm:2",
            "regions": [BOX],
            "callout_box": CARD,
        },
        {
            "action": "geometry",
            "finding_id": "vlm:1",
            "regions": [{**BOX, "x_max": 1001}],
            "callout_box": None,
        },
        {
            "action": "add",
            "finding_id": "manual:not-a-uuid",
            "page_number": 2,
            "text": "x",
            "normative_basis": "",
            "issue_box": BOX,
            "callout_box": CARD,
        },
        {
            "action": "add",
            "finding_id": f"manual:{uuid4()}",
            "page_number": 2,
            "text": "x",
            "normative_basis": "",
            "issue_box": BOX,
            "callout_box": CARD,
        },
    ],
)
async def test_invalid_or_forged_commands_leave_review_unchanged(flow, body):
    """Нельзя подменить инженера/оригинал, придумать область или выйти за лист."""
    async with client(flow) as browser:
        await browser.post(flow.endpoint + "/open")
        response = await command(browser, flow, 0, **body)
        assert response.status_code == 422, response.text
    assert (await flow.reviews.load(flow.job_id)).revision == 0


@pytest.mark.parametrize("revision", [True, "0", -1])
async def test_revision_is_strict_nonnegative_integer(flow, revision):
    """Булево, строка и отрицательная ревизия не превращаются в успешную CAS-запись."""
    async with client(flow) as browser:
        await browser.post(flow.endpoint + "/open")
        response = await command(
            browser,
            flow,
            revision,
            action="decide",
            finding_id="vlm:1",
            decision="accepted",
        )
        assert response.status_code == 422
    assert (await flow.reviews.load(flow.job_id)).revision == 0


async def test_channel_keys_context_and_cross_site_protection(flow):
    """Публичный фронт, неверный ключ и запрос другого сайта не получают Review."""
    async with client(flow) as browser:
        response = await browser.get(
            "/api/v1/review/config", headers={"X-PDRD-Review-Key": ""}
        )
        assert response.json() == {"enabled": False}
        for headers in (
            {"X-PDRD-Review-Key": ""},
            {"X-PDRD-Review-Key": b"\xff"},
            {"Origin": "http://evil.test"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            assert (
                await browser.post(flow.endpoint + "/open", headers=headers)
            ).status_code == 403
        assert (
            await browser.get(f"/internal/v1/review-source/{flow.job_id}")
        ).status_code == 403
        await browser.post(
            flow.endpoint + "/open", headers={"X-Review-Actor": "attacker"}
        )
        accepted = await command(
            browser, flow, 0, action="decide", finding_id="vlm:1", decision="accepted"
        )
        assert accepted.json()["findings"][0]["updated_by"] == "engineer:test"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(flow.network.apps["experience"]),
        base_url="http://experience",
    ) as internal:
        assert (
            await internal.get(
                f"/internal/v1/reviews/{flow.job_id}",
                headers={"X-Review-Actor": "attacker"},
            )
        ).status_code == 403


async def test_conflict_incomplete_job_and_unknown_job(flow):
    """Конфликт возвращает 409 и не теряет первый результат; доступ проверяется заново."""
    async with client(flow) as browser:
        await browser.post(flow.endpoint + "/open")
        first = await command(
            browser, flow, 0, action="decide", finding_id="vlm:1", decision="accepted"
        )
        assert first.status_code == 200
        assert (
            await command(
                browser,
                flow,
                0,
                action="decide",
                finding_id="vlm:1",
                decision="rejected",
            )
        ).status_code == 409
        assert (
            await browser.post(f"/api/v1/analyses/{uuid4()}/review/open")
        ).status_code == 404
        flow.jobs.job.status = AnalysisJobStatus.PROCESSING
        assert (await browser.get(flow.endpoint)).status_code == 409


async def test_vlm_geometry_remains_unconfirmed_and_approval_is_invalidated(flow):
    """Правки VLM не повышают координаты до Experience, а изменение отменяет утверждение."""
    async with client(flow) as browser:
        await browser.post(flow.endpoint + "/open")
        await command(
            browser, flow, 0, action="decide", finding_id="vlm:1", decision="accepted"
        )
        await command(
            browser, flow, 1, action="decide", finding_id="vlm:2", decision="rejected"
        )
        approved = await command(browser, flow, 2, action="approve")
        assert approved.json()["approved_revision"] == 3
        changed = await command(
            browser,
            flow,
            3,
            action="geometry",
            finding_id="vlm:1",
            regions=[{**BOX, "x_max": 180}],
            callout_box=CARD,
        )
        state = changed.json()
        assert state["approved_revision"] is None
        assert state["findings"][0]["issue_box"] is None
        assert state["findings"][0]["proposed_regions"][0]["bbox"] == BOX
        assert state["findings"][0]["display_regions"][0]["x_max"] == 180
        assert (await command(browser, flow, 4, action="approve")).status_code == 422


def test_review_requires_explicit_closed_environment_and_is_not_production_auth():
    """Конфигурация по умолчанию закрыта; prod и отсутствие ключей запрещены."""
    assert not GatewaySettings(_env_file=None).review.enabled
    assert not ExperienceSettings(_env_file=None).review_api_enabled
    with pytest.raises(ValueError):
        GatewaySettings(_env_file=None, review={"enabled": True})
    with pytest.raises(ValueError):
        GatewaySettings(
            _env_file=None,
            environment="prod",
            review={
                "enabled": True,
                "controlled_access": True,
                "actor": "engineer",
                "ui_key": UI_KEY,
                "internal_key": INTERNAL_KEY,
            },
        )
    with pytest.raises(ValueError):
        ExperienceSettings(_env_file=None, review_api_enabled=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", "engineer\nforged-header"),
        ("ui_key", "x" * 40 + '"'),
        ("internal_key", "x" * 40 + "\n"),
    ],
)
def test_controlled_headers_and_nginx_keys_reject_invalid_characters(field, value):
    """Серверная конфигурация не допускает управляющих символов в nginx/HTTP."""
    settings = {
        "enabled": True,
        "controlled_access": True,
        "actor": "engineer:test",
        "ui_key": UI_KEY,
        "internal_key": INTERNAL_KEY,
    }
    settings[field] = value
    with pytest.raises(ValueError):
        GatewaySettings(_env_file=None, review=settings)
