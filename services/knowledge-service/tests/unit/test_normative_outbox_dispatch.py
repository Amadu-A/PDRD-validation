# services/knowledge-service/tests/unit/test_normative_outbox_dispatch.py

"""Unit tests Knowledge transactional outbox dispatcher."""

from collections.abc import Callable
from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from types import TracebackType
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.ports.messaging import (
    NormativeOutboxPublishError,
)
from pdrd_knowledge_service.application.use_cases.dispatch_normative_outbox import (
    DispatchNormativeOutbox,
)
from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)

CREATED_TIME = datetime(
    2026,
    9,
    2,
    12,
    0,
    tzinfo=UTC,
)

PUBLISHED_TIME = CREATED_TIME + timedelta(
    seconds=1,
)

DOCUMENT_ID = UUID(
    "55555555-5555-5555-5555-555555555555",
)

MESSAGE_ID = UUID(
    "66666666-6666-6666-6666-666666666666",
)


@dataclass
class FakeState:
    """In-memory outbox dispatcher state."""

    messages: dict[
        UUID,
        NormativeOutboxMessage,
    ] = field(
        default_factory=dict,
    )

    commits: int = 0


class FakeOutboxRepository:
    """Fake outbox repository."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Сохраняет state."""
        self._state = state

    async def get_pending(
        self,
        *,
        limit: int,
    ) -> list[NormativeOutboxMessage]:
        """Возвращает unpublished messages."""
        return [
            message
            for message in self._state.messages.values()
            if message.published_at is None
        ][:limit]

    async def update(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Обновляет message."""
        self._state.messages[message.message_id] = message


class PlaceholderRepository:
    """Неиспользуемый repository fake Unit of Work."""

    pass


class FakeUnitOfWork:
    """Fake Unit of Work dispatcher."""

    def __init__(
        self,
        state: FakeState,
    ) -> None:
        """Создаёт repositories."""
        self._state = state

        self.sections = PlaceholderRepository()
        self.categories = PlaceholderRepository()
        self.documents = PlaceholderRepository()

        self.outbox = FakeOutboxRepository(
            state,
        )

    async def __aenter__(
        self,
    ) -> "FakeUnitOfWork":
        """Открывает transaction."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Закрывает transaction."""
        return None

    async def commit(
        self,
    ) -> None:
        """Учитывает commit."""
        self._state.commits += 1

    async def rollback(
        self,
    ) -> None:
        """Fake rollback."""
        return None


class FakePublisher:
    """Fake publisher с управляемой ошибкой."""

    def __init__(
        self,
        *,
        fail: bool,
    ) -> None:
        """Сохраняет режим работы."""
        self._fail = fail

        self.published: list[UUID] = []

    async def publish(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Публикует message или эмулирует broker failure."""
        if self._fail:
            raise NormativeOutboxPublishError(
                "RabbitMQ unavailable.",
            )

        self.published.append(
            message.message_id,
        )


def build_factory(
    state: FakeState,
) -> Callable[
    [],
    FakeUnitOfWork,
]:
    """Создаёт Unit of Work factory."""
    return lambda: FakeUnitOfWork(
        state,
    )


def make_message() -> NormativeOutboxMessage:
    """Создаёт pending outbox message."""
    return NormativeOutboxMessage.index_requested(
        message_id=MESSAGE_ID,
        document_id=DOCUMENT_ID,
        created_at=CREATED_TIME,
    )


@pytest.mark.asyncio
async def test_dispatch_marks_message_published() -> None:
    """Успешная публикация фиксируется в outbox."""
    state = FakeState()

    state.messages[MESSAGE_ID] = make_message()

    publisher = FakePublisher(
        fail=False,
    )

    report = await DispatchNormativeOutbox(
        unit_of_work_factory=build_factory(
            state,
        ),
        publisher=publisher,
        clock=lambda: PUBLISHED_TIME,
    ).execute(
        limit=20,
    )

    message = state.messages[MESSAGE_ID]

    assert report.selected == 1
    assert report.published == 1
    assert report.failed == 0

    assert publisher.published == [
        MESSAGE_ID,
    ]

    assert message.attempt_count == 1
    assert message.last_error is None
    assert message.published_at == PUBLISHED_TIME

    assert state.commits == 1


@pytest.mark.asyncio
async def test_dispatch_keeps_failed_message_pending() -> None:
    """Broker failure оставляет message pending для retry."""
    state = FakeState()

    state.messages[MESSAGE_ID] = make_message()

    report = await DispatchNormativeOutbox(
        unit_of_work_factory=build_factory(
            state,
        ),
        publisher=FakePublisher(
            fail=True,
        ),
        clock=lambda: PUBLISHED_TIME,
    ).execute(
        limit=20,
    )

    message = state.messages[MESSAGE_ID]

    assert report.selected == 1
    assert report.published == 0
    assert report.failed == 1

    assert message.attempt_count == 1
    assert message.published_at is None
    assert message.last_error == "RabbitMQ unavailable."

    assert state.commits == 1
