# services/experience-service/tests/unit/test_review_export_scenario_regression.py

"""Повторяет SQL-сценарий межстраничного Review с настоящими проверками ревизий без БД."""

from pdrd_experience_service.application.use_cases.review import ChangeReview
from pdrd_experience_service.infrastructure.database.codec import (
    snapshot_from_json,
    snapshot_to_json,
)
from pdrd_experience_service.infrastructure.database.models import ReviewEventModel
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)

from tests.functional.reviewed_pdf_support import (
    MemoryAreas,
    MemoryReviewConfirmationCommitter,
)

from ..integration import test_confirmed_areas_database as database_support
from ..integration import test_review_export_database as database_export
from .test_review_persistence import FakeDatabase


class SnapshotReviewRepository(SqlAlchemyReviewRepository):
    """Исполняет SQL-проверки и преобразование снимков, заменяя только сессию БД."""

    def __init__(self, database):
        """Подключает наблюдаемые SQL-операции и локальное чтение снимка."""
        super().__init__(lambda: database)
        self.snapshot = None
        self.history = ()

    async def load(self, job_id):
        """Восстанавливает Review через тот же кодек, что используется для JSONB."""
        if self.snapshot is None:
            return None
        review = snapshot_from_json(self.snapshot, self.history)
        return review if review.job_id == job_id else None

    async def insert(self, session):
        """Проверяет начальную ревизию и порядок записи события открытия."""
        await super().insert(session)
        self.snapshot = snapshot_to_json(session)
        self.history = session.history

    async def update(self, session, *, expected_revision):
        """Исполняет настоящую защиту от записи неизменённой редакции и аудита."""
        current = await self.load(session.job_id)
        assert current.revision == expected_revision
        await super().update(session, expected_revision=expected_revision)
        self.snapshot = snapshot_to_json(session)
        self.history = session.history


async def test_cross_page_sql_scenario_accepts_then_approves_once(monkeypatch):
    """Тот же интеграционный сценарий создаёт только opened, decided и approved."""
    database = FakeDatabase()
    reviews = SnapshotReviewRepository(database)
    areas = MemoryAreas(reviews)
    changes = ChangeReview(reviews, MemoryReviewConfirmationCommitter(reviews, areas))

    cleaned_jobs = []

    async def cleanup(engine, job_id):
        """Фиксирует очистку тестового задания без обращения к настоящей БД."""
        cleaned_jobs.append(job_id)

    monkeypatch.setattr(database_export, "adapters", lambda engine: (reviews, areas))
    monkeypatch.setattr(database_export, "cleanup", cleanup)
    monkeypatch.setattr(database_support, "review_changes", lambda *args: changes)

    await database_export.test_cross_page_review_provenance_roundtrip_and_export_in_postgresql(
        None
    )

    events = [row for row in database.added if isinstance(row, ReviewEventModel)]
    assert [(row.session_revision, row.action) for row in events] == [
        (0, "opened"),
        (1, "decided"),
        (2, "approved"),
    ]
    assert cleaned_jobs == [events[0].job_id]
