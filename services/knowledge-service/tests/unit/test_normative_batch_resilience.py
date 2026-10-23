# services/knowledge-service/tests/unit/test_normative_batch_resilience.py

"""Размер SQL-порций, неоднозначный commit и адресный повтор без сброса готовой базы."""

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
import yaml
from pdrd_knowledge_service.application import normative_requirement_batches as batches
from pdrd_knowledge_service.application.normative_generation_integrity import (
    NormativeGenerationIntegrityError,
    update_digest,
)
from pdrd_knowledge_service.application.ports.persistence import (
    CatalogConnectionLostError,
)
from pdrd_knowledge_service.application.use_cases import (
    normative_requirements as use_cases,
)
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
    NormativeRequirementsConflictError,
)
from pdrd_knowledge_service.application.use_cases.recover_normative_requirements import (
    RecoverNormativeRequirements,
)
from pdrd_knowledge_service.core.settings import NormativeRequirementsSettings, Settings
from pdrd_knowledge_service.domain.normative_indexing import NormativeTextPage
from pdrd_knowledge_service.domain.normative_requirement_extraction import (
    extract_normative_requirements,
)
from pdrd_knowledge_service.domain.normative_requirements import (
    EXTRACTOR_VERSION,
    content_digest,
    stable_requirement_id,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
    catalog_connection_lost,
)
from pdrd_knowledge_service.infrastructure.messaging import worker_runtime
from pdrd_knowledge_service.transport.cli.normative_requirements import parser, run
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError

from ..atomic_normative_support import (
    DOCUMENT_ID,
    PAGES,
    Extractor,
    State,
    Storage,
    UnitOfWork,
    document,
)

ROOT = Path(__file__).resolve().parents[4]


def many_pages():
    """Настоящий экстрактор формирует атомы, а тест измеряет только их сохранение."""
    return (
        NormativeTextPage(
            1,
            "СП 99.13330.2026\n5 Требования\n"
            + "\n".join(
                f"5.{index} Для насоса {index} следует предусматривать резерв."
                for index in range(1, 206)
            ),
        ),
    )


def atomic_rows(document_id=DOCUMENT_ID, pages=PAGES):
    """Возвращает детерминированные атомы неизменного тестового источника."""
    return tuple(
        extract_normative_requirements(
            document_id=document_id,
            source_sha256=document().sha256,
            source_file="normative.pdf",
            pages=pages,
        )
    )


@pytest.mark.parametrize("size", [1, 10, 20, 100])
async def test_batch_sizes_keep_ids_digest_ordinals_and_transaction_boundaries(size):
    """Размер порции не меняет итог и не оставляет SQL-транзакцию на извлечении PDF."""
    state, writes = State(), []

    def factory():
        uow = UnitOfWork(state)
        write = uow.requirements.write_batch

        async def record(generation_id, offset, requirements):
            """Запоминает реально переданную SQL-порцию."""
            writes.append((offset, len(requirements)))
            await write(generation_id, offset, requirements)

        uow.requirements.write_batch = record
        return uow

    class OutsideTransaction(Extractor):
        """Внешнее извлечение не удерживает UOW каталога."""

        async def extract_pages(self, **kwargs):
            """Отсекает случайную длинную SQL-транзакцию."""
            assert not state.lock.locked()
            return await super().extract_pages(**kwargs)

    queued = await NormativeRequirementCatalog(factory).queue(document_id=DOCUMENT_ID)
    result = await BuildNormativeRequirements(
        factory,
        Storage(),
        OutsideTransaction(many_pages()),
        batch_size=size,
    ).execute(document_id=DOCUMENT_ID, generation_id=queued["generation_id"])
    expected, digest = atomic_rows(pages=many_pages()), hashlib.sha256()
    for item in expected:
        update_digest(digest, item.as_dict())
    saved = state.rows[result["generation_id"]]
    assert len(saved) == result["count"] == len(expected) > 200
    assert sorted(row[0] for row in saved.values()) == list(range(len(expected)))
    assert set(saved) == {item.requirement_id for item in expected}
    assert result["manifest"]["requirements_digest"] == digest.hexdigest()
    assert writes == [
        (offset, min(size, len(expected) - offset))
        for offset in range(0, len(expected), size)
    ]


@pytest.mark.parametrize(
    "mode", ["rollback", "lost_ack", "corrupt_ack", "always_lost", "programming"]
)
async def test_only_unconfirmed_batch_retries_with_bounded_backoff(monkeypatch, mode):
    """Откат, подтверждённый commit без ACK и программный дефект имеют разные исходы."""
    state, offsets, sleeps = State(), [], []

    async def backoff(seconds):
        """Измеряет интервалы без задержки unit-теста."""
        sleeps.append(seconds)

    monkeypatch.setattr(use_cases.asyncio, "sleep", backoff)

    def factory():
        uow = UnitOfWork(state)
        write = uow.requirements.write_batch
        commit = uow.commit
        written = False

        async def inserted(generation_id, offset, rows):
            """Сбой после INSERT ещё не подтверждает транзакцию."""
            nonlocal written
            offsets.append(offset)
            await write(generation_id, offset, rows)
            written = True
            if mode == "programming":
                raise KeyError("неожиданный дефект")
            if mode == "always_lost" or (mode == "rollback" and len(offsets) == 1):
                raise CatalogConnectionLostError("соединение потеряно")

        async def committed():
            """Имитирует потерю ответа после фактической фиксации."""
            await commit()
            if written and len(offsets) == 1 and mode in {"lost_ack", "corrupt_ack"}:
                if mode == "corrupt_ack":
                    rows = next(iter(state.rows.values()))
                    key = next(iter(rows))
                    ordinal, payload = rows[key]
                    rows[key] = (
                        ordinal,
                        {**payload, "text": "повреждённое содержимое"},
                    )
                raise CatalogConnectionLostError("commit уже мог пройти")

        uow.requirements.write_batch = inserted
        uow.commit = committed
        return uow

    queued = await NormativeRequirementCatalog(factory).queue(document_id=DOCUMENT_ID)
    worker = BuildNormativeRequirements(factory, Storage(), Extractor(), batch_size=1)
    if mode in {"corrupt_ack", "always_lost", "programming"}:
        expected = {
            "corrupt_ack": NormativeGenerationIntegrityError,
            "always_lost": batches.NormativeBatchWriteFailedError,
            "programming": KeyError,
        }[mode]
        with pytest.raises(expected):
            await worker.execute(
                document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
            )
        assert state.generations[queued["generation_id"]]["state"] == "failed"
        assert not any(row["active"] for row in state.generations.values())
        assert offsets == ([0, 0, 0] if mode == "always_lost" else [0])
        assert sleeps == (
            [0.5, 1]
            if mode == "always_lost"
            else [0.5]
            if mode == "corrupt_ack"
            else []
        )
        return
    result = await worker.execute(
        document_id=DOCUMENT_ID, generation_id=queued["generation_id"]
    )
    assert result["state"] == "ready" and result["attempt_count"] == 1
    assert offsets == ([0] if mode == "rollback" else []) + list(range(result["count"]))
    assert sleeps == [0.5]
    assert len(state.rows[result["generation_id"]]) == result["count"]


async def test_large_jsonb_splits_without_truncation_and_oversized_atom_stops(
    monkeypatch,
):
    """Порция ограничена по байтам; один слишком большой атом не получает бесконечный retry."""
    template = atomic_rows()[0]
    rows = tuple(
        replace(
            template,
            context=("Ж" * 100_000,),
            point_id=stable_requirement_id(str(i)),
            requirement_id="N-R-" + stable_requirement_id(str(i)),
        )
        for i in range(20)
    )
    portions = list(batches.bounded_requirement_batches(rows))
    assert len(portions) > 1 and all(
        size <= batches.MAX_BATCH_BYTES for _, size in portions
    )
    assert tuple(row for part, _ in portions for row in part) == rows
    state = State()

    def factory():
        """Открывает отдельную тестовую транзакцию."""
        return UnitOfWork(state)

    queued = await NormativeRequirementCatalog(factory).queue(document_id=DOCUMENT_ID)
    huge = replace(template, context=("Ж" * batches.MAX_ATOM_BYTES,))
    monkeypatch.setattr(
        use_cases, "extract_normative_requirements", lambda **kwargs: iter((huge,))
    )
    with pytest.raises(
        batches.NormativeAtomTooLargeError, match=r"json_bytes=.*limit="
    ):
        await BuildNormativeRequirements(factory, Storage(), Extractor()).execute(
            document_id=DOCUMENT_ID,
            generation_id=queued["generation_id"],
        )
    failed = state.generations[queued["generation_id"]]
    assert failed["state"] == "failed" and failed["retryable"] is False
    assert state.writes == 0


async def test_restart_targets_two_exhausted_documents_and_preserves_283_ready(
    tmp_path,
):
    """Новый UUID попытки не меняет готовые поколения, их manifest, атомы и историю отказов."""
    state = State(documents={}, sections=State().sections)
    for number in range(285):
        identifier = UUID(int=number + 2000)
        state.documents[str(identifier)] = document(document_id=identifier)
        rows = atomic_rows(identifier)
        digest = hashlib.sha256()
        for row in rows:
            update_digest(digest, row.as_dict())
        generation_id = stable_requirement_id(
            str(identifier), document().sha256, EXTRACTOR_VERSION, content_digest([])
        )
        state.generations[generation_id] = {
            "generation_id": generation_id,
            "fingerprint": generation_id,
            "document_id": str(identifier),
            "source_sha256": document().sha256,
            "extractor_version": EXTRACTOR_VERSION,
            "state": "ready" if number < 283 else "failed",
            "active": number < 283,
            "attempt_count": 1 if number < 283 else 4,
            "dispatch_count": 1 if number < 283 else 3,
            "count": len(rows) if number < 283 else 0,
            "updated_at": document().updated_at.isoformat(),
            "created_at": document().created_at.isoformat(),
            "status_evidence": [],
            "attempt_id": None,
            "manifest": {
                "pages": [1, 2],
                "requirements_digest": digest.hexdigest(),
                "source_sha256": document().sha256,
                "extractor_version": EXTRACTOR_VERSION,
                "coverage": "complete",
            },
        }
        state.rows[generation_id] = (
            {
                row.requirement_id: (i, json.loads(json.dumps(row.as_dict())))
                for i, row in enumerate(rows)
            }
            if number < 283
            else {}
        )
    ready = copy.deepcopy(
        {key: value for key, value in state.generations.items() if value["active"]}
    )
    ready_rows = copy.deepcopy({key: state.rows[key] for key in ready})

    def factory():
        """Открывает отдельную тестовую транзакцию."""
        return UnitOfWork(state)

    catalog, recovery = (
        NormativeRequirementCatalog(factory),
        RecoverNormativeRequirements(NormativeRequirementCatalog(factory)),
    )
    for identifier in (UUID(int=2283), UUID(int=2284)):
        previous = (await catalog.index_state(document_id=identifier))[
            "requested_generation"
        ]
        assert (await catalog.queue(document_id=identifier))["state"] == "failed"
        args = parser().parse_args(
            [
                "backfill",
                "--document-id",
                str(identifier),
                "--restart-exhausted",
                "--no-wait",
                "--report",
                str(tmp_path / f"{identifier}.json"),
            ]
        )
        await run(args, recovery)
        new = (await catalog.index_state(document_id=identifier))[
            "requested_generation"
        ]
        assert new["generation_id"] != previous["generation_id"]
        assert (
            new["previous_generation_id"] == previous["generation_id"]
            and new["attempt_count"] == 0
        )
        result = await BuildNormativeRequirements(
            factory, Storage(), Extractor()
        ).execute(document_id=identifier, generation_id=new["generation_id"])
        assert result["state"] == "ready"
        assert state.generations[previous["generation_id"]] == previous
        with pytest.raises(NormativeRequirementsConflictError):
            await catalog.queue(document_id=identifier, restart_exhausted=True)
    assert {key: state.generations[key] for key in ready} == ready
    assert {key: state.rows[key] for key in ready} == ready_rows
    report = await recovery.inspect()
    assert all(
        row["verified_requirements_digest"] == row["requirements_digest"]
        for row in report["documents"]
    )
    assert report["states"] == {"ready": 285} and all(
        row["integrity"] == "verified" for row in report["documents"]
    )


@pytest.mark.parametrize("scope", ["--all-sections", "--section-id"])
async def test_global_restart_is_rejected(tmp_path, scope):
    """CLI не позволяет сбросить всю промышленную библиотеку одним флагом."""
    args = ["backfill", scope]
    if scope == "--section-id":
        args.append(str(document().section_id))
    args += ["--restart-exhausted", "--report", str(tmp_path / "report.json")]
    with pytest.raises(ValueError, match="один --document-id"):
        await run(
            parser().parse_args(args),
            RecoverNormativeRequirements(
                NormativeRequirementCatalog(lambda: UnitOfWork(State()))
            ),
        )


@pytest.mark.parametrize("value", [0, 201, -1])
def test_settings_reject_unsafe_batch_size(value):
    """Настройка допускает только диапазон 1–200."""
    with pytest.raises(ValidationError):
        NormativeRequirementsSettings(batch_size=value)


async def test_environment_compose_and_worker_di_use_configured_batch(monkeypatch):
    """Переменная проходит весь путь env → Settings → настоящий runtime → use case."""
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    key = "KNOWLEDGE_SERVICE_NORMATIVE_REQUIREMENTS__BATCH_SIZE"
    assert (
        compose["services"]["knowledge-indexer"]["environment"][key]
        == "${" + key + ":-20}"
    )
    monkeypatch.setenv(key, "10")
    settings = Settings(_env_file=None)
    assert settings.normative_requirements.batch_size == 10
    assert NormativeRequirementsSettings().batch_size == 20
    engine = SimpleNamespace(dispose=AsyncMock())
    constructor = Mock(
        return_value=SimpleNamespace(execute=AsyncMock(return_value={"state": "ready"}))
    )
    monkeypatch.setattr(worker_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_runtime, "build_async_engine", lambda value: engine)
    monkeypatch.setattr(worker_runtime, "build_session_factory", lambda value: Mock())
    monkeypatch.setattr(worker_runtime, "BuildNormativeRequirements", constructor)
    await worker_runtime.execute_normative_requirements_indexing(
        document_id=DOCUMENT_ID, generation_id=str(DOCUMENT_ID)
    )
    assert constructor.call_args.kwargs["batch_size"] == 10
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "invalidated,state,expected",
    [
        (True, None, True),
        (False, "08003", True),
        (False, "57P01", True),
        (False, "23505", False),
        (False, "57014", False),
    ],
)
def test_driver_error_classification(invalidated, state, expected):
    """Повторы не маскируют SQL-конфликты и таймауты как отключение PostgreSQL."""
    driver = RuntimeError("driver")
    driver.sqlstate = state
    assert (
        catalog_connection_lost(
            DBAPIError("INSERT", {}, driver, connection_invalidated=invalidated)
        )
        is expected
    )
    assert not catalog_connection_lost(KeyError("bug"))


async def test_uow_closes_even_when_rollback_fails_and_preserves_programming_error():
    """Ошибка cleanup не мешает закрыть session и не подменяет исходный KeyError."""
    failure = DBAPIError(
        "INSERT", {}, RuntimeError("closed"), connection_invalidated=True
    )
    session = SimpleNamespace(
        in_transaction=lambda: True,
        rollback=AsyncMock(side_effect=failure),
        close=AsyncMock(),
    )
    uow = SqlAlchemyNormativeCatalogUnitOfWork(lambda: session)
    with pytest.raises(CatalogConnectionLostError):
        async with uow:
            raise failure
    session.close.assert_awaited_once()
    with pytest.raises(KeyError, match="original"):
        async with uow:
            raise KeyError("original")
    assert session.close.await_count == 2
    assert not catalog_connection_lost(
        IntegrityError("INSERT", {}, RuntimeError("bad data"))
    )
