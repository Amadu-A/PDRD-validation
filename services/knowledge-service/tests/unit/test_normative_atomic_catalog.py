# services/knowledge-service/tests/unit/test_normative_atomic_catalog.py

"""Поколения, откат, повтор, статус и изоляция атомарного нормативного каталога."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
    NormativeRequirementsConflictError,
)
from pdrd_knowledge_service.domain.normative_catalog import CatalogArea

from ..atomic_normative_support import (
    ACTOR_ID,
    DOCUMENT_ID,
    Extractor,
    State,
    Storage,
    UnitOfWork,
    proof_payload,
)


def catalog(state):
    """Подменяет только транзакционную границу, сохраняя production-сценарии."""
    return NormativeRequirementCatalog(lambda: UnitOfWork(state))


def worker(state, **changes):
    """Индексация порциями по одному атому усиливает проверку частичных отказов."""
    return BuildNormativeRequirements(
        **{
            "unit_of_work_factory": lambda: UnitOfWork(state),
            "storage": Storage(),
            "pdf_extractor": Extractor(),
            "batch_size": 1,
            **changes,
        }
    )


async def build(state):
    """Ставит явное задание и исполняет его без broker/GPU."""
    generation = await catalog(state).queue(document_id=DOCUMENT_ID)
    return await worker(state).execute(
        document_id=DOCUMENT_ID, generation_id=generation["generation_id"]
    )


async def test_queue_build_and_pagination_cover_complete_generation_without_similarity_or_gpu():
    """Все исходные атомы, включая рекомендации, проходят полную воспроизводимую пагинацию."""
    state = State()
    with pytest.raises(LookupError):
        await catalog(state).list(document_id=DOCUMENT_ID)
    assert state.outbox == [] and state.generations == {}
    queued = await catalog(state).queue(document_id=DOCUMENT_ID)
    assert queued["state"] == "queued" and len(state.outbox) == 1
    assert state.outbox[0].payload["generation_id"] == queued["generation_id"]
    assert (await catalog(state).queue(document_id=DOCUMENT_ID)) == queued
    assert len(state.outbox) == 1
    adapter = Extractor()
    ready = await worker(state, pdf_extractor=adapter).execute(
        document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
    )
    assert ready["state"] == "ready" and ready["active"]
    assert ready["manifest"]["pages"] == [1, 2]
    assert ready["manifest"]["vlm_calls"] == ready["manifest"]["embedding_calls"] == 0
    rows, offset = [], 0
    while True:
        page = await catalog(state).list(
            document_id=DOCUMENT_ID,
            generation_id=ready["generation_id"],
            offset=offset,
            limit=2,
        )
        assert page["count"] == ready["count"]
        rows.extend(page["requirements"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(rows) == ready["count"] == len({r["requirement_id"] for r in rows})
    assert any(row["modality"] == "advisory" for row in rows)
    assert all(row["normative_status"]["edition_status"] == "unknown" for row in rows)
    writes = state.writes
    await worker(state, pdf_extractor=adapter).execute(
        document_id=DOCUMENT_ID, generation_id=ready["generation_id"]
    )
    assert state.writes == writes and adapter.calls == 1


async def test_failed_new_generation_preserves_old_and_retry_recovers_all_batches():
    """Частичный индекс не заменяет рабочий; повтор добирает строки и публикует один active."""
    state = State()
    old = await build(state)
    old_rows = (await catalog(state).list(document_id=DOCUMENT_ID))["requirements"]
    await catalog(state).record_status(
        document_id=DOCUMENT_ID, payload=proof_payload(), actor_id=ACTOR_ID
    )
    queued = await catalog(state).queue(document_id=DOCUMENT_ID)
    assert queued["generation_id"] != old["generation_id"]
    state.fail_batch = state.writes + 2
    with pytest.raises(RuntimeError, match="batch failure"):
        await worker(state).execute(
            document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
        )
    assert state.generations[queued["generation_id"]]["state"] == "failed"
    preserved = await catalog(state).list(document_id=DOCUMENT_ID)
    assert (
        preserved["generation_id"] == old["generation_id"]
        and preserved["status_evidence_changed"]
    )
    assert [r["requirement_id"] for r in preserved["requirements"]] == [
        r["requirement_id"] for r in old_rows
    ]
    assert all(
        r["normative_status"]["requirement_binding"] != "mandatory"
        for r in preserved["requirements"]
    )
    state.fail_batch = None
    retried = await catalog(state).queue(document_id=DOCUMENT_ID)
    assert retried["generation_id"] == queued["generation_id"]
    result = await worker(state).execute(
        document_id=DOCUMENT_ID, generation_id=retried["generation_id"]
    )
    assert result["count"] == old["count"]
    assert sum(row["active"] for row in state.generations.values()) == 1
    assert len(state.rows[old["generation_id"]]) == old["count"]
    historical = await catalog(state).list(
        document_id=DOCUMENT_ID, generation_id=old["generation_id"]
    )
    assert historical["requirements"] == preserved["requirements"]


async def test_status_is_append_only_scoped_and_cannot_turn_stale_snapshot_into_proof():
    """Добавленное опровержение немедленно закрывает подтверждение до явного нового snapshot."""
    state = State()
    first = await catalog(state).record_status(
        document_id=DOCUMENT_ID, payload=proof_payload(), actor_id=ACTOR_ID
    )
    duplicate = await catalog(state).record_status(
        document_id=DOCUMENT_ID, payload=proof_payload(), actor_id=ACTOR_ID
    )
    assert first == duplicate and len(state.proofs) == 1
    await build(state)
    page = await catalog(state).list(document_id=DOCUMENT_ID, assessed_on="2026-10-20")
    assert any(
        row["normative_status"]["requirement_binding"] == "mandatory"
        for row in page["requirements"]
        if row["clause"] == "5.1"
    )
    assert all(
        row["normative_status"]["requirement_binding"] != "mandatory"
        for row in page["requirements"]
        if row["clause"] != "5.1"
    )
    await catalog(state).record_status(
        document_id=DOCUMENT_ID,
        payload=proof_payload(edition_status="withdrawn"),
        actor_id=ACTOR_ID,
    )
    stale = await catalog(state).list(document_id=DOCUMENT_ID)
    assert stale["status_evidence_changed"]
    assert all(
        row["normative_status"]["edition_status"] == "unknown"
        for row in stale["requirements"]
    )
    await build(state)
    updated = await catalog(state).list(document_id=DOCUMENT_ID)
    assert not updated["status_evidence_changed"]
    assert all(
        row["normative_status"]["edition_status"] == "disputed"
        for row in updated["requirements"]
        if row["clause"] == "5.1"
    )


async def test_source_substitution_or_incomplete_extraction_never_replaces_active_generation():
    """Замена байтов и пустой PDF остаются диагностируемыми отказами, без пустого успеха."""
    for failure in ("source", "empty"):
        state = State()
        old = await build(state)
        await catalog(state).record_status(
            document_id=DOCUMENT_ID, payload=proof_payload(), actor_id=ACTOR_ID
        )
        queued = await catalog(state).queue(document_id=DOCUMENT_ID)
        broken = (
            worker(state, storage=Storage(b"changed"))
            if failure == "source"
            else worker(state, pdf_extractor=Extractor(()))
        )
        with pytest.raises((NormativeRequirementsConflictError, ValueError)):
            await broken.execute(
                document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
            )
        assert (await catalog(state).list(document_id=DOCUMENT_ID))[
            "generation_id"
        ] == old["generation_id"]


async def test_source_update_isolated_from_old_generation_and_explicit_rollback_reuses_ready_index():
    """Другой SHA требует нового поколения; возврат исходника явно активирует прежнее готовое."""
    state = State()
    original_document = state.documents[str(DOCUMENT_ID)]
    old = await build(state)
    updated_source = b"updated normative file"
    state.documents[str(DOCUMENT_ID)] = replace(
        original_document, sha256=hashlib.sha256(updated_source).hexdigest()
    )
    with pytest.raises(NormativeRequirementsConflictError):
        await catalog(state).list(document_id=DOCUMENT_ID)
    historical = await catalog(state).list(
        document_id=DOCUMENT_ID, generation_id=old["generation_id"]
    )
    assert historical["historical_source"]
    queued = await catalog(state).queue(document_id=DOCUMENT_ID)
    new = await worker(
        state,
        storage=Storage(updated_source),
        pdf_extractor=Extractor(content=updated_source),
    ).execute(document_id=DOCUMENT_ID, generation_id=queued["generation_id"])
    assert old["generation_id"] != new["generation_id"]
    assert old["count"] == new["count"]
    state.documents[str(DOCUMENT_ID)] = original_document
    outbox_count = len(state.outbox)
    restored = await catalog(state).queue(document_id=DOCUMENT_ID)
    assert restored["generation_id"] == old["generation_id"] and restored["active"]
    assert len(state.outbox) == outbox_count
    assert (await catalog(state).list(document_id=DOCUMENT_ID))["generation_id"] == old[
        "generation_id"
    ]
    assert not state.generations[new["generation_id"]]["active"]


async def test_stale_worker_cannot_fail_or_publish_new_attempt():
    """После явного восстановления просроченной попытки старый worker теряет право записи."""
    state, started, resume = State(), asyncio.Event(), asyncio.Event()

    class PausedExtractor(Extractor):
        """Моделирует остановку процесса между получением lease и первой записью."""

        async def extract_pages(self, **kwargs):
            """Даёт другой команде восстановить поколение без реального ожидания десяти минут."""
            started.set()
            await resume.wait()
            return await super().extract_pages(**kwargs)

    queued = await catalog(state).queue(document_id=DOCUMENT_ID)
    old = asyncio.create_task(
        worker(state, pdf_extractor=PausedExtractor()).execute(
            document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(NormativeRequirementsConflictError):
        await catalog(state).queue(document_id=DOCUMENT_ID)
    state.generations[queued["generation_id"]]["updated_at"] = (
        datetime.now(UTC) - timedelta(minutes=11)
    ).isoformat()
    await catalog(state).queue(document_id=DOCUMENT_ID)
    fresh = await worker(state).execute(
        document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
    )
    resume.set()
    with pytest.raises(NormativeRequirementsConflictError):
        await old
    assert state.generations[queued["generation_id"]] == fresh


async def test_explicit_retry_recovers_stale_queued_delivery_without_implicit_read_work():
    """Потерянная доставка восстанавливается явной командой; GET не дублирует outbox."""
    state = State()
    queued = await catalog(state).queue(document_id=DOCUMENT_ID)
    state.generations[queued["generation_id"]]["updated_at"] = (
        datetime.now(UTC) - timedelta(minutes=11)
    ).isoformat()
    assert (await catalog(state).index_state(document_id=DOCUMENT_ID))[
        "state"
    ] == "queued"
    assert len(state.outbox) == 1
    # Неопубликованное сообщение уже будет повторено dispatcher; дубликат не нужен.
    assert (await catalog(state).queue(document_id=DOCUMENT_ID))[
        "generation_id"
    ] == queued["generation_id"]
    assert len(state.outbox) == 1
    state.outbox[0].mark_published(published_at=datetime.now(UTC))
    retried = await catalog(state).queue(document_id=DOCUMENT_ID)
    assert (
        retried["generation_id"] == queued["generation_id"] and len(state.outbox) == 2
    )
    result = await worker(state).execute(
        document_id=DOCUMENT_ID, generation_id=retried["generation_id"]
    )
    assert result["state"] == "ready" and result["count"] > 0


async def test_other_document_generation_private_packages_and_deleting_sections_are_isolated():
    """Знание generation_id не открывает чужую запись и не переносит личные документы в N."""
    state = State()
    ready = await build(state)
    other = uuid4()
    state.documents[str(other)] = replace(
        state.documents[str(DOCUMENT_ID)], document_id=other
    )
    with pytest.raises(LookupError):
        await catalog(state).list(
            document_id=other, generation_id=ready["generation_id"]
        )
    state.documents[str(DOCUMENT_ID)] = replace(
        state.documents[str(DOCUMENT_ID)],
        area=CatalogArea.USER_PACKAGE,
        owner_user_id=ACTOR_ID,
    )
    with pytest.raises(LookupError):
        await catalog(state).queue(document_id=DOCUMENT_ID)
    state.documents[str(DOCUMENT_ID)] = replace(
        state.documents[str(DOCUMENT_ID)],
        area=CatalogArea.NORMATIVE,
        owner_user_id=None,
    )
    state.deleting = True
    with pytest.raises(NormativeRequirementsConflictError):
        await catalog(state).queue(document_id=DOCUMENT_ID)


@pytest.mark.parametrize(("offset", "limit"), [(-1, 100), (0, 0), (0, 201)])
async def test_catalog_pagination_is_bounded(offset, limit):
    """Ошибочные размеры страницы не превращают каталог в неограниченную выдачу."""
    with pytest.raises(ValueError):
        await catalog(State()).list(document_id=DOCUMENT_ID, offset=offset, limit=limit)
