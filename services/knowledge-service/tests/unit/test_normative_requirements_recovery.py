# services/knowledge-service/tests/unit/test_normative_requirements_recovery.py

"""Восстановление 285 документов, bounded admission, crash/retry и полнота реальных атомов."""

import asyncio
import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from pdrd_knowledge_service.application.normative_generation_integrity import (
    NormativeGenerationIntegrityError,
)
from pdrd_knowledge_service.application.normative_preview import preview_provenance_key
from pdrd_knowledge_service.application.normative_requirement_policy import (
    NormativeRequirementPolicy,
)
from pdrd_knowledge_service.application.use_cases.dispatch_normative_outbox import (
    DispatchNormativeOutbox,
)
from pdrd_knowledge_service.application.use_cases.index_normative_document import (
    IndexNormativeDocument,
)
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
)
from pdrd_knowledge_service.application.use_cases.recover_normative_requirements import (
    RecoverNormativeRequirements,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    IndexingStatus,
    NormativeSection,
)
from pdrd_knowledge_service.domain.normative_indexing import NormativeTextPage
from pdrd_knowledge_service.infrastructure.storage.filesystem import (
    LocalFilesystemNormativeDocumentStorage,
)
from pdrd_knowledge_service.transport.cli.normative_requirements import parser, run

from ..atomic_normative_support import (
    DOCUMENT_ID,
    PAGES,
    SOURCE,
    Extractor,
    Repository,
    State,
    Storage,
    UnitOfWork,
    document,
)


def setup(state, **policy):
    """Подменяет только persistence; сценарии, атомизация и manifest остаются production."""
    catalog = NormativeRequirementCatalog(
        lambda: UnitOfWork(state), policy=NormativeRequirementPolicy(**policy)
    )
    return (
        catalog,
        RecoverNormativeRequirements(catalog),
        BuildNormativeRequirements(
            catalog.unit_of_work_factory, Storage(), Extractor()
        ),
    )


def library():
    """Семнадцать динамических разделов, включая пустой; 284 READY и один FAILED."""
    state = State(documents={}, sections={})
    for number in range(17):
        identifier = UUID(int=number + 1000)
        state.sections[str(identifier)] = NormativeSection(
            identifier,
            f"Раздел {number}",
            "Промпт",
            document().created_at,
            document().updated_at,
        )
    sections = list(state.sections.values())
    for number in range(285):
        item = document(
            document_id=UUID(int=number + 2000),
            section_id=sections[number % 16].section_id,
        )
        if number == 284:
            item = replace(
                item,
                index_status=IndexingStatus.FAILED,
                index_error="Исходный chunk index не готов.",
                indexed_at=None,
            )
        state.documents[str(item.document_id)] = item
    return state


async def test_dry_run_285_documents_and_empty_section_has_no_side_effects(tmp_path):
    """Административный CLI сохраняет только отчёт; БД, задачи и corpus остаются неизменными."""
    state = library()
    _, recovery, _ = setup(state)
    before = copy.deepcopy(
        (state.documents, state.generations, state.rows, state.outbox)
    )
    args = parser().parse_args(
        [
            "backfill",
            "--all-sections",
            "--dry-run",
            "--report",
            str(tmp_path / "dry-run.json"),
        ]
    )
    report = await run(args, recovery)
    assert len(report["sections"]) == 17 and len(report["documents"]) == 285
    assert report["eligible"] == 284 and report["in_flight"] == 0
    assert report["sections"][-1]["documents"] == 0
    assert report["documents"][-1]["reason"] == "chunk_index_not_ready"
    assert (state.documents, state.generations, state.rows, state.outbox) == before
    assert json.loads(args.report.read_text(encoding="utf-8"))["qdrant_mutations"] == 0


async def test_backfill_all_284_documents_preserves_library_and_bounds_every_iteration():
    """Настоящий разбор сохранённых страниц выполняется worker; ни одна пачка не допускает >2 jobs."""
    state = library()
    catalog, recovery, worker = setup(state)
    completed = set()
    for _ in range(145):
        report = await recovery.execute(limit=2)
        admitted = [
            row
            for row in state.generations.values()
            if row["state"] == "queued" and not row["dispatch_pending"]
        ]
        assert len(admitted) <= 2 and report["selected"] <= 2
        for row in admitted:
            result = await worker.execute(
                document_id=UUID(row["document_id"]), generation_id=row["generation_id"]
            )
            assert result["state"] == "ready" and result["count"] > 0
            completed.add(row["document_id"])
        if len(completed) == 284:
            break
    assert len(completed) == 284
    report = await recovery.inspect()
    assert report["states"] == {"ready": 284, "not_indexed": 1}
    assert all(
        row["integrity"] == "verified"
        for row in report["documents"]
        if row["atomic_state"] == "ready"
    )
    assert all(row["legal_status"] == "unknown" for row in report["documents"])
    before = len(state.outbox)
    assert (await recovery.execute())["selected"] == 0
    assert len(state.outbox) == before and len(state.documents) == 285
    assert (await catalog.index_state(document_id=UUID(int=2000)))["state"] == "ready"


async def test_parallel_admission_and_pending_intent_survive_restart():
    """Параллельные upload/manual requests не обходят лимит; новый dispatcher подбирает durable intent."""
    state = library()
    catalog, _, worker = setup(state)
    identifiers = [UUID(key) for key in list(state.documents)[:5]]
    rows = await asyncio.gather(
        *(catalog.queue(document_id=key) for key in identifiers)
    )
    assert sum(not row["dispatch_pending"] for row in rows) == len(state.outbox) == 2
    await worker.execute(
        document_id=identifiers[0], generation_id=rows[0]["generation_id"]
    )
    _, restarted, _ = setup(state)
    await restarted.execute(only_pending=True)
    assert len(state.outbox) == 3
    assert (
        sum(
            row["state"] in {"queued", "building"} and not row["dispatch_pending"]
            for row in state.generations.values()
        )
        == 2
    )
    assert len(state.generations) == 5


@pytest.mark.parametrize(
    "phase", ["none", "queued", "building", "failed", "retry_exhausted"]
)
async def test_reconciliation_recovers_missing_stale_and_failed_with_backoff(phase):
    """Потеря broker delivery, crash и временный отказ имеют разные условия повтора."""
    state = State()
    catalog, recovery, _ = setup(state)
    if phase != "none":
        queued = await catalog.queue(document_id=DOCUMENT_ID)
        state.outbox[0].mark_published(published_at=datetime.now(UTC))
        generation = state.generations[queued["generation_id"]]
        generation.update(
            state="queued" if phase == "retry_exhausted" else phase,
            updated_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        )
        if phase == "failed":
            generation.update(
                retry_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                retryable=True,
            )
            assert (await recovery.execute())["selected"] == 0
            generation["retry_at"] = (
                datetime.now(UTC) - timedelta(seconds=1)
            ).isoformat()
        if phase == "retry_exhausted":
            generation["dispatch_count"] = 3
    report = await recovery.execute()
    current = (await catalog.index_state(document_id=DOCUMENT_ID))[
        "requested_generation"
    ]
    assert report["selected"] == 1
    if phase == "retry_exhausted":
        assert (
            current["state"] == "failed"
            and current["error_code"] == "attempt_limit_exhausted"
        )
        assert (await recovery.execute())["selected"] == 0
    else:
        assert current["state"] == "queued"


async def test_ready_transition_outbox_rollback_and_redelivery_are_one_lifecycle(
    monkeypatch,
):
    """Ошибка вставки outbox откатывает READY и generation; повтор READY не вызывает embedding/Qdrant."""
    state = State()
    state.documents[str(DOCUMENT_ID)] = replace(
        document(), index_status=IndexingStatus.INDEXING, indexed_at=None
    )
    catalog, _, _ = setup(state)
    forbidden = object()
    indexer = IndexNormativeDocument(
        catalog.unit_of_work_factory,
        Storage(),
        Extractor(),
        forbidden,
        forbidden,
        "unchanged",
        1000,
        0,
        1,
        1,
        atomic_requirements=catalog,
    )
    original_add = Repository.add

    async def fail(self, message):
        """Моделирует отказ SQL-вставки намерения, а не ошибку внешнего broker."""
        raise RuntimeError("outbox insert failed")

    monkeypatch.setattr(Repository, "add", fail)
    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await indexer._mark_ready(document_id=DOCUMENT_ID)
    assert state.documents[str(DOCUMENT_ID)].index_status is IndexingStatus.INDEXING
    assert not state.outbox and not state.generations
    monkeypatch.setattr(Repository, "add", original_add)
    await indexer._mark_ready(document_id=DOCUMENT_ID)
    assert (
        state.documents[str(DOCUMENT_ID)].index_status is IndexingStatus.READY
        and len(state.outbox) == 1
    )
    await indexer.execute(document_id=DOCUMENT_ID)
    assert len(state.outbox) == 1


async def test_transactional_intent_dispatch_and_worker_keep_all_rows():
    """Проверяет реальный dispatcher и worker после commit; повторное чтение ничего не запускает."""
    state = State()
    catalog, _, worker = setup(state)
    queued = await catalog.queue(document_id=DOCUMENT_ID)
    delivered = []

    class Publisher:
        """Имитирует только Rabbit transport, не application lifecycle."""

        async def publish(self, message):
            """Сохраняет payload существующего события."""
            delivered.append(message)

    report = await DispatchNormativeOutbox(
        catalog.unit_of_work_factory, Publisher()
    ).execute(limit=2)
    assert report.published == 1 and state.outbox[0].published_at
    assert delivered[0].payload["generation_id"] == queued["generation_id"]
    ready = await worker.execute(
        document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
    )
    assert ready["count"] == len(
        (await catalog.list(document_id=DOCUMENT_ID))["requirements"]
    )
    assert (
        await DispatchNormativeOutbox(
            catalog.unit_of_work_factory, Publisher()
        ).execute(limit=2)
    ).selected == 0


@pytest.mark.parametrize(
    "corruption", ["count", "payload", "page", "sha", "duplicate", "missing_manifest"]
)
async def test_corrupt_ready_is_repaired_in_new_generation_without_erasing_old(
    corruption,
):
    """Проверка фактических строк не доверяет READY и не очищает старое опубликованное поколение."""
    state = State()
    catalog, recovery, worker = setup(state)
    queued = await catalog.queue(document_id=DOCUMENT_ID)
    ready = await worker.execute(
        document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
    )
    generation = state.generations[ready["generation_id"]]
    rows = state.rows[ready["generation_id"]]
    first = next(iter(rows.values()))[1]
    if corruption == "count":
        generation["count"] += 1
    elif corruption == "payload":
        first["source_text"] = "Подменённая цитата"
    elif corruption == "page":
        first["source_fragments"][0]["page"] = 999
    elif corruption == "sha":
        first["source_sha256"] = "b" * 64
    elif corruption == "duplicate":
        rows["duplicate"] = (999, copy.deepcopy(first))
    else:
        generation["manifest"] = {}
    assert (await recovery.inspect())["documents"][0]["atomic_state"] == "corrupt"
    replacement = await catalog.queue(document_id=DOCUMENT_ID)
    assert (
        replacement["generation_id"] != ready["generation_id"] and generation["active"]
    )
    new = await worker.execute(
        document_id=DOCUMENT_ID, generation_id=replacement["generation_id"]
    )
    assert (
        new["state"] == "ready"
        and (await recovery.inspect())["documents"][0]["integrity"] == "verified"
    )
    assert ready["generation_id"] in state.rows


async def test_corrupted_write_never_publishes_partial_generation(monkeypatch):
    """Проверка producer digest обнаруживает повреждение между формированием и записью atom JSON."""
    state = State()
    catalog, _, worker = setup(state)
    queued = await catalog.queue(document_id=DOCUMENT_ID)
    write = Repository.write_batch

    async def corrupt(self, generation_id, offset, requirements):
        """Меняет сохранённую цитату; исходный extractor при этом настоящий."""
        await write(self, generation_id, offset, requirements)
        next(iter(self.uow.data["rows"][generation_id].values()))[1]["source_text"] = (
            "Потерянный текст"
        )

    monkeypatch.setattr(Repository, "write_batch", corrupt)
    with pytest.raises(NormativeGenerationIntegrityError):
        await worker.execute(
            document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
        )
    assert not state.generations[queued["generation_id"]]["active"]
    assert state.generations[queued["generation_id"]]["state"] == "failed"


@pytest.mark.parametrize("all_scanned", [False, True])
async def test_empty_native_pages_are_explicitly_incomplete_and_never_silently_ocr_all(
    all_scanned,
):
    """Native-only worker сохраняет страницы без текста, не объявляет скан полноценно обработанным."""
    state = State()
    catalog, recovery, worker = setup(state)
    pages = (
        (NormativeTextPage(1, ""), NormativeTextPage(2, ""))
        if all_scanned
        else (*PAGES, NormativeTextPage(3, ""))
    )
    worker = replace(worker, pdf_extractor=Extractor(pages))
    queued = await catalog.queue(document_id=DOCUMENT_ID)
    if all_scanned:
        with pytest.raises(ValueError, match="ocr_required"):
            await worker.execute(
                document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
            )
    else:
        await worker.execute(
            document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
        )
    row = (await recovery.inspect())["documents"][0]
    assert row["source_coverage"] == "incomplete" and row["missing_text_pages"]
    assert row["atomic_state"] == ("failed" if all_scanned else "ready")
    assert (await recovery.execute())["selected"] == 0


@pytest.mark.parametrize(
    "extension,mime",
    [
        ("doc", "application/msword"),
        (
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
async def test_word_preview_provenance_reuses_verified_pdf_and_rebuilds_stale_preview(
    tmp_path, extension, mime
):
    """Старый preview восстанавливается штатным converter, исходный SHA и файл остаются прежними."""
    state = State()
    source = document(
        original_name="Норматив." + extension,
        mime_type=mime,
        storage_key="norm." + extension,
    )
    state.documents[str(DOCUMENT_ID)] = source
    catalog, _, worker = setup(state)
    storage = LocalFilesystemNormativeDocumentStorage(root_path=tmp_path)
    await storage.save(storage_key=source.storage_key, content=SOURCE)
    await storage.save(
        storage_key=source.storage_key + ".preview.pdf", content=b"stale preview"
    )
    converted = b"verified pdf"
    calls = []

    async def convert(**kwargs):
        """Имитирует только внешний LibreOffice процесс."""
        calls.append(kwargs)
        return converted

    worker = replace(
        worker,
        storage=storage,
        office_converter=SimpleNamespace(convert_to_pdf=convert),
        pdf_extractor=Extractor(content=converted),
    )
    queued = await catalog.queue(document_id=DOCUMENT_ID)
    ready = await worker.execute(
        document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
    )
    assert ready["state"] == "ready" and len(calls) == 1
    provenance = json.loads(
        await storage.read(storage_key=preview_provenance_key(source.storage_key))
    )
    assert provenance == {
        "source_sha256": source.sha256,
        "pdf_sha256": hashlib.sha256(converted).hexdigest(),
    }
    assert await storage.read(storage_key=source.storage_key) == SOURCE
    # Проверка повторного чтения preview отдельно от идемпотентного worker READY.
    from pdrd_knowledge_service.application.normative_preview import verified_preview

    assert (
        await verified_preview(storage, worker.office_converter, source, SOURCE)
        == converted
    )
    assert len(calls) == 1


async def test_changed_status_recovers_existing_intent_even_when_all_admission_slots_are_occupied():
    """Старый intent освобождает собственный слот в той же транзакции, что новая постановка."""
    from ..atomic_normative_support import ACTOR_ID, proof_payload

    state = State()
    catalog, recovery, _ = setup(state, max_in_flight=1)
    old = await catalog.queue(document_id=DOCUMENT_ID)
    await catalog.record_status(
        document_id=DOCUMENT_ID, payload=proof_payload(), actor_id=ACTOR_ID
    )
    result = await recovery.execute(only_pending=True)
    fresh = (await catalog.index_state(document_id=DOCUMENT_ID))["requested_generation"]
    assert result["selected"] == 1 and fresh["generation_id"] != old["generation_id"]
    assert (
        state.generations[old["generation_id"]]["error_code"]
        == "superseded_source_or_status"
    )
    assert not fresh["dispatch_pending"]


async def test_cli_wait_resumes_from_database_and_keeps_failed_chunk_document_separate(
    tmp_path, monkeypatch
):
    """CLI ставит по одному job, ждёт terminal, сохраняет отчёт, затем повторяется без дубликатов."""
    from pdrd_knowledge_service.transport.cli import normative_requirements as cli

    state = State()
    for index in range(3):
        item = document(document_id=UUID(int=5000 + index))
        state.documents[str(item.document_id)] = item
    failed = document(
        document_id=UUID(int=9999),
        index_status=IndexingStatus.FAILED,
        indexed_at=None,
        index_error="Ошибка chunk index",
    )
    state.documents[str(failed.document_id)] = failed
    _, recovery, worker = setup(state)

    async def complete_admitted(delay):
        """Имитирует ход времени и отдельного worker; построение не происходит внутри CLI execute."""
        admitted = [
            copy.deepcopy(row)
            for row in state.generations.values()
            if row["state"] == "queued" and not row["dispatch_pending"]
        ]
        assert len(admitted) <= 1
        for row in admitted:
            await worker.execute(
                document_id=UUID(row["document_id"]), generation_id=row["generation_id"]
            )

    monkeypatch.setattr(cli, "asyncio", SimpleNamespace(sleep=complete_admitted))
    args = parser().parse_args(
        [
            "backfill",
            "--all-sections",
            "--only-missing",
            "--retry-failed",
            "--max-in-flight",
            "1",
            "--report",
            str(tmp_path / "backfill.json"),
        ]
    )
    report = await run(args, recovery)
    assert report["finished"] and not report["complete"]
    assert report["states"] == {"ready": 4, "not_indexed": 1}
    assert len(state.outbox) == 4
    await run(args, recovery)
    assert len(state.outbox) == 4
