# services/experience-service/tests/unit/test_review_history_http.py

"""HTTP возвращает компактный аудит решения, сохраняя CAS и доверенный контекст."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdrd_experience_service.application.use_cases.review import ChangeReview
from pdrd_experience_service.domain.review import (
    OriginalFinding,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.transport.http.routers.review import router
from pydantic import SecretStr


class MemoryReviews:
    """Порт хранения проверяет CAS; решения исполняет настоящий ChangeReview."""

    def __init__(self):
        """Создаёт нелокализованное замечание без необходимости подтверждать область."""
        self.session = ReviewSession.open(
            job_id=UUID(int=1),
            document_id=UUID(int=2),
            source_filename="project.pdf",
            source_sha256="a" * 64,
            originals=(OriginalFinding("vlm:1", 22, "Исходный текст", "СП 1"),),
            rendered_pages=(22,),
            actor="user:server",
            at=datetime(2026, 10, 13, tzinfo=UTC),
        )

    async def load(self, job_id):
        """Читает сохранённый снимок, а не входные данные браузера."""
        return self.session if job_id == self.session.job_id else None

    async def update(self, session, *, expected_revision):
        """Не допускает перезапись параллельной редакции."""
        if expected_revision != self.session.revision:
            raise ReviewConflictError("stale revision")
        self.session = session


def client_and_repository():
    """Подключает production router и сценарии к изолированному порту хранения."""
    repository = MemoryReviews()
    app = FastAPI()
    app.state.container = SimpleNamespace(
        settings=SimpleNamespace(internal_key=SecretStr("private-key")),
        reviews=repository,
        change_review=ChangeReview(repository),
        confirmed_areas=None,
        confirm_area=None,
        revoke_area=None,
        approval_experience=None,
    )
    app.include_router(router)
    return TestClient(app), repository


def test_review_history_survives_read_edit_and_redecision_without_full_audit():
    """Отказ виден после чтения; правка возвращает pending и не стирает прежнее решение."""
    client, repository = client_and_repository()
    url = f"/internal/v1/reviews/{repository.session.job_id}"
    headers = {"Authorization": "Bearer private-key", "X-Review-Actor": "user:server"}
    with client:
        initial = client.get(url, headers=headers).json()
        assert initial["pending_count"] == 1
        assert initial["decision_history"] == []

        rejected = client.post(
            url + "/commands",
            headers=headers,
            json={
                "action": "decide",
                "expected_revision": 0,
                "finding_id": "vlm:1",
                "decision": "rejected",
                "reason_category": "false_positive",
                "comment": "Проверено по проекту",
            },
        )
        assert rejected.status_code == 200
        assert rejected.json() == client.get(url, headers=headers).json()
        assert rejected.json()["findings"][0]["decision"] == "rejected"
        event = rejected.json()["decision_history"][0]
        assert event["comment"] == "Проверено по проекту"
        assert event["actor"] == "user:server"
        assert event["decision"] == "rejected"
        assert set(event) == {
            "finding_id",
            "action",
            "actor",
            "occurred_at",
            "session_revision",
            "decision",
            "reason_category",
            "comment",
        }
        assert "history" not in rejected.json()
        assert "before" not in event and "after" not in event

        stale = client.post(
            url + "/commands",
            headers=headers,
            json={"action": "reset", "expected_revision": 0, "finding_id": "vlm:1"},
        )
        assert stale.status_code == 409
        edited = client.post(
            url + "/commands",
            headers=headers,
            json={
                "action": "edit",
                "expected_revision": 1,
                "finding_id": "vlm:1",
                "text": "Уточнённый текст",
                "normative_basis": "СП 1",
            },
        )
        assert edited.status_code == 200
        assert edited.json()["findings"][0]["decision"] == "pending"
        assert edited.json()["findings"][0]["original_text"] == "Исходный текст"
        accepted = client.post(
            url + "/commands",
            headers=headers,
            json={
                "action": "decide",
                "expected_revision": 2,
                "finding_id": "vlm:1",
                "decision": "accepted",
            },
        )
        assert accepted.status_code == 200
        restored = client.get(url, headers=headers).json()
        assert restored == accepted.json()
        assert [event["decision"] for event in restored["decision_history"]] == [
            "rejected",
            "pending",
            "accepted",
        ]
        assert repository.session.history[1].before.original_text == "Исходный текст"


def test_compact_history_is_not_available_without_gateway_key_and_actor():
    """Добавление журнала не открывает внутренние данные браузеру или пустому актору."""
    client, repository = client_and_repository()
    url = f"/internal/v1/reviews/{repository.session.job_id}"
    with client:
        assert client.get(url).status_code == 403
        assert (
            client.get(url, headers={"Authorization": "Bearer private-key"}).status_code
            == 403
        )
        assert (
            client.get(
                url,
                headers={"Authorization": "Bearer wrong", "X-Review-Actor": "user:x"},
            ).status_code
            == 403
        )
