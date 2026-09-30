# services/knowledge-service/tests/unit/test_experience_versions.py

"""Ручная очередь не индексирует посторонние замечания и не публикует неполный состав."""

from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.application.ports.experience_versions import VersionIndexJob
from pdrd_knowledge_service.application.use_cases.index_experience_version import (
    RunExperienceVersion,
    SelectedExperienceFeed,
)

from tests.experience_index_support import trusted_example

from .test_trusted_experience import services


class Queue:
    """Порт аренды с наблюдаемыми финальными статусами."""

    def __init__(self, job=None):
        """Хранит необязательное ручное задание."""
        self.job, self.results = job, []

    async def claim(self, **kwargs):
        """Выдаёт только заранее созданную задачу."""
        return self.job

    async def finish(self, *, job_id, worker, status, error=""):
        """Сохраняет последовательность статусов worker."""
        self.results.append(status)


async def test_empty_manual_queue_never_calls_embedding_or_catalog_scan():
    """Новые примеры каталога сами не создают задачу индексации."""
    feed, embedding, vectors, index, _ = services((trusted_example(),))
    result = await RunExperienceVersion(Queue(), index, "worker", "model").execute()
    assert result == {"claimed": False}
    assert not feed.page_calls and not embedding.calls and not vectors.writes


async def test_selected_version_contains_only_selected_examples():
    """Весь остальной каталог остаётся вне новой коллекции."""
    selected, other = trusted_example(), trusted_example(number=2)
    feed, embedding, vectors, index, _ = services((selected, other))
    queue = Queue(
        VersionIndexJob(uuid4(), "manual", "identity", 2, "section", (selected,))
    )
    result = await RunExperienceVersion(queue, index, "worker", "model").execute()
    assert result["examples"] == 1 and result["indexed"] == 1
    assert not feed.page_calls and len(embedding.calls) == 1
    assert {record.payload["example_id"] for record in vectors.records.values()} == {
        str(selected.reference.example_id)
    }
    assert queue.results == ["ready"]


async def test_version_verification_excludes_live_members_of_other_versions():
    """Даже корректный живой payload соседней коллекции не расширяет выбранный состав."""
    selected, other = trusted_example(), trusted_example(number=2)
    feed, _, _, _, _ = services((selected, other))
    scoped = SelectedExperienceFeed(feed, (selected,))
    assert await scoped.verify((selected.reference, other.reference)) == (selected,)
    assert feed.verify_calls == [(selected.reference,)]


@pytest.mark.parametrize("moment", ["before_build", "during_embedding", "before_ready"])
async def test_revoked_member_never_marks_version_ready(moment):
    """Изменение выбранной редакции в любой фазе запрещает публикацию полного состава."""
    selected = trusted_example()
    feed, embedding, _, index, _ = services((selected,))
    if moment == "before_build":
        feed.current.clear()
    if moment == "during_embedding":
        embedding.after_embed = feed.current.clear
    if moment == "before_ready":
        verify = feed.verify

        async def revoke(references):
            if len(feed.verify_calls) >= 2:
                feed.current.clear()
            return await verify(references)

        feed.verify = revoke
    queue = Queue(
        VersionIndexJob(uuid4(), "manual", "identity", 2, "section", (selected,))
    )
    with pytest.raises(ExperienceFeedError):
        await RunExperienceVersion(queue, index, "worker", "model").execute()
    assert queue.results == ["failed"]
