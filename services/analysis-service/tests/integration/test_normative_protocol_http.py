# services/analysis-service/tests/integration/test_normative_protocol_http.py

"""Analysis → Gateway → persistent artifacts: настоящий HTTP, без готовых findings."""

import asyncio
import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pdrd_analysis_service.application.independent_normative import (
    IndependentNormativeReview,
)
from pdrd_analysis_service.application.ports.normative_protocol import (
    NormativeProtocolUnavailable,
)
from pdrd_analysis_service.application.vision_execution import AnalysisCancelledError
from pdrd_analysis_service.domain.normative_protocol import (
    canonical_json,
    protocol_archive,
)
from pdrd_analysis_service.infrastructure.normative_protocol import (
    HttpNormativeProtocolWriter,
)
from pdrd_api_gateway.application.normative_protocol import ManageNormativeProtocol
from pdrd_api_gateway.application.ports.normative_protocol import (
    NormativeProtocolError,
    NormativeProtocolNotFound,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import GetAnalysisJob
from pdrd_api_gateway.core.container import ApplicationContainer as GatewayContainer
from pdrd_api_gateway.core.settings import DatabaseSettings
from pdrd_api_gateway.core.settings import Settings as GatewaySettings
from pdrd_api_gateway.domain.analysis_job import AnalysisJob, AnalysisJobStatus
from pdrd_api_gateway.domain.normative_snapshot import NormativeAnalysisSnapshot
from pdrd_api_gateway.infrastructure.storage.normative_protocol import (
    LocalNormativeProtocolStore,
)
from pdrd_api_gateway.main import create_app

from ..unit.test_analysis_use_cases import FakeVisionModel, page_facts
from ..unit.test_independent_normative import SECTION, no_cancel
from ..unit.test_normative_transport_budget import (
    CorpusReader,
    corpus_result,
)
from .test_stage_batch_concurrency_http import (
    RecordingProgressProbe,
    _build_app,
    _image,
)


def run_resolver(source_name, response, batches, mode="pdf"):
    """Исполняет публикуемый Code node: HTTP выполняет штатный HTTP Request, а не sandbox."""
    workflow = json.loads(
        (
            Path(__file__).parents[4] / f"n8n/workflows/analysis-v2-{mode}.json"
        ).read_text("utf-8")
    )
    resolver = {
        "Check Norms": "Resolve Norm Check Response",
        "Finalize Findings": "Resolve Finalization Response",
        "Check Cross-Page Consistency": "Resolve Cross-Page Response",
    }[source_name]
    code = next(n for n in workflow["nodes"] if n["name"] == resolver)["parameters"][
        "jsCode"
    ]
    script = """
const fs = require('node:fs'), vm = require('node:vm'), crypto = require('node:crypto');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ctx = {Buffer, $input: {all: () => input.batches.map(json => ({json}))},
  $: name => ({first: () => ({json: name.startsWith('POST ')
    ? {body: {document_id: input.document_id}} : input.response})})};
const out = vm.runInNewContext(`(function(){${input.code}})()`, ctx, {timeout: 20000})[0].json;
const pages = out.items ?? [{page_number: out.findings[0]?.page ?? 1, result: out}];
const sorted = value => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
process.stdout.write(JSON.stringify({pages: pages.map(i => i.page_number),
 findings: pages.reduce((n, i) => n+i.result.findings.length, 0),
 digest: crypto.createHash('sha256').update(JSON.stringify(sorted(pages))).digest('hex')}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(
            {
                "code": code,
                "document_id": str(UUID(int=900)),
                "response": response,
                "batches": batches,
            }
        ),
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


async def test_streaming_budget_counts_real_dto_including_computed_source_kinds():
    """Измерение совпадает с Pydantic JSON и останавливается до создания огромной строки."""
    from pdrd_analysis_service.transport.http.response_budget import response_bytes
    from pdrd_analysis_service.transport.http.routes import _finding_draft_payload
    from pdrd_analysis_service.transport.http.schemas import CheckNormsResponse
    from pdrd_analysis_service.transport.http.stage_batch_schemas import (
        CheckNormsStageResponse,
    )

    from ..unit.test_analysis_use_cases import finding

    response = CheckNormsStageResponse(
        items=[
            {
                "page_number": 1,
                "result": CheckNormsResponse(
                    summary='Кавычки: " и перевод\nстроки',
                    findings=[_finding_draft_payload(finding())],
                    metrics={"bytes": 1.25},
                ),
            }
        ]
    )
    assert await response_bytes(response) == len(
        response.model_dump_json().encode("utf-8")
    )
    assert (
        100
        < await response_bytes(response, budget=100)
        < len(response.model_dump_json().encode("utf-8"))
    )


class Jobs:
    """Существующие порты UoW/Repository, общее состояние заменяет только PostgreSQL."""

    def __init__(self, job):
        """Связывает job и document как в промышленном репозитории."""
        self.job = job
        self.analysis_jobs = self

    def __call__(self):
        """Открывает ту же тестовую транзакционную границу."""
        return self

    async def __aenter__(self):
        """Возвращает репозиторий тестового задания."""
        return self

    async def __aexit__(self, *args):
        """Закрывает границу без скрытых записей."""

    async def get(self, job_id):
        """Не считает document UUID идентификатором задания."""
        return self.job if self.job.id == job_id else None

    async def get_by_document_id_for_update(self, document_id):
        """Публикация может заблокировать только собственное задание."""
        return self.job if self.job.document_id == document_id else None


async def shutdown():
    """Тест не запускает внешние ресурсы."""


@pytest.fixture
async def gateway(tmp_path):
    """Реальный volume adapter и HTTP router; scope берётся из durable job."""
    doc = UUID(int=900)
    documents = tuple(UUID(int=700 + n) for n in range(11))
    job = AnalysisJob.create(
        document_id=doc,
        normative_snapshot=NormativeAnalysisSnapshot(
            UUID(SECTION), documents, "Учебная проверка"
        ),
        guest_access=True,
    )
    jobs = Jobs(job)
    store = LocalNormativeProtocolStore(tmp_path)
    from pdrd_api_gateway.domain.analysis_submission import AnalysisSubmission
    from pdrd_api_gateway.infrastructure.storage.filesystem import (
        LocalFilesystemAnalysisArtifactStore,
    )

    artifacts = LocalFilesystemAnalysisArtifactStore(root_path=store.root_path)
    submission = replace(
        AnalysisSubmission.create(
            pdf_present=True,
            cad_present=False,
            pages=None,
            pdf_file_name="synthetic-control.pdf",
            cad_file_name=None,
            use_document_context=True,
        ),
        document_id=doc,
    )
    await artifacts.save_request(
        submission=submission,
        pdf_content=b"synthetic fixture, not a real PDF",
        cad_content=None,
    )
    manager = ManageNormativeProtocol(jobs, store)
    container = GatewayContainer(
        settings=GatewaySettings(
            _env_file=None,
            environment="test",
            database=DatabaseSettings(password="test"),
        ),
        check_readiness=None,
        shutdown_callback=shutdown,
        get_analysis_job=GetAnalysisJob(jobs),
        normative_protocol=manager,
    )
    app = create_app(container)
    writer = HttpNormativeProtocolWriter(
        "http://gateway.test", transport=httpx.ASGITransport(app=app)
    )
    return SimpleNamespace(
        app=app,
        writer=writer,
        jobs=jobs,
        store=store,
        manager=manager,
        root=tmp_path,
        artifacts=artifacts,
    )


def archive(result, document_ids=None):
    """Использует production-канонизацию всех источников, включая неприменимые."""
    return protocol_archive(
        document_id=str(UUID(int=900)),
        section_id=SECTION,
        document_ids=document_ids or tuple(str(UUID(int=700 + n)) for n in range(11)),
        diagnostics=result.diagnostics,
        sources=result.sources,
    )


async def collect(store, document_id, protocol_id, collection):
    """Дочитывает все страницы; next_offset является частью проверяемого контракта."""
    rows, offset = [], 0
    while True:
        page = await store.read(document_id, protocol_id, collection, offset, 200)
        assert len(canonical_json(page)) <= 1024 * 1024 + 4096
        rows.extend(page["items"])
        if page["next_offset"] is None:
            return rows
        assert page["next_offset"] > offset
        offset = page["next_offset"]


async def test_protocol_survives_restart_and_exactly_restores_all_proof_records(
    gateway,
):
    """Повторный adapter читает ровно исходные данные: несколько observations не схлопываются."""
    result = await corpus_result(count=23)
    first = result.diagnostics["checks"][0]
    first["observations"] = [
        {"page": 1, "fact": {"evidence_text": "Первый исходный факт"}},
        {"page": 2, "fact": {"evidence_text": "Второй исходный факт"}},
    ]
    ref = await gateway.writer.save(
        archive=archive(result), check_cancellation=no_cancel
    )
    assert ref["counts"]["checks"] == 23
    # Повтор записи имеет тот же ID; новый процесс не зависит от Python-кеша старого.
    again = await gateway.writer.save(
        archive=archive(result), check_cancellation=no_cancel
    )
    assert ref == again
    restarted = LocalNormativeProtocolStore(gateway.root)
    assert await collect(
        restarted, UUID(int=900), ref["protocol_id"], "checks"
    ) == json.loads(json.dumps(result.diagnostics["checks"]))
    assert await collect(
        restarted, UUID(int=900), ref["protocol_id"], "sources"
    ) == list(result.sources)
    summary = await gateway.manager.hydrate_metrics(
        gateway.jobs.job.id,
        {
            "metrics": {
                "independent_normative": {
                    "requirement_plan": {**result.summary, "protocol": ref}
                }
            }
        },
    )
    from pdrd_api_gateway.application.normative_diagnostics import normative_diagnostics

    assert (
        normative_diagnostics(summary, job_id=str(gateway.jobs.job.id))["requirements"]
        == 23
    )


async def test_10412_requirements_31_pages_real_http_response_is_bounded_and_lossless(
    gateway,
):
    """Полный корпус и реальные DTO Check Norms, FakeVisionModel ничего не обнаруживает."""
    model = FakeVisionModel(
        [{"summary": "Учебный лист проверен", "violations": []} for _ in range(31)]
    )
    app = _build_app(
        model=model, progress_probe=RecordingProgressProbe(), concurrency=4
    )
    from pdrd_analysis_service.infrastructure.normative_requirements import (
        HttpNormativeRequirementsReader,
    )

    documents = {}
    for index in range(11):
        document_id = str(UUID(int=700 + index))
        documents[document_id] = await CorpusReader().load(document_id=document_id)
    knowledge_calls = []

    def knowledge(request):
        """Реальный reader читает bounded API Knowledge, а не заранее готовый протокол."""
        knowledge_calls.append(request.url.path)
        if request.url.path.endswith("/documents"):
            return httpx.Response(
                200,
                json=[{"document_id": key, "section_id": SECTION} for key in documents],
            )
        doc_id = request.url.path.split("/")[-2]
        document = documents[doc_id]
        offset = int(request.url.params.get("offset", 0))
        rows = document.requirements[offset : offset + 200]
        end = offset + len(rows)
        return httpx.Response(
            200,
            json={
                **document.manifest,
                "document_id": doc_id,
                "state": "ready",
                "status_evidence": document.requirements[0]["status_evidence"],
                "offset": offset,
                "next_offset": end if end < len(document.requirements) else None,
                "requirements": rows,
            },
        )

    app.state.container = replace(
        app.state.container,
        independent_normative_review=IndependentNormativeReview(
            HttpNormativeRequirementsReader(
                "http://knowledge.test", transport=httpx.MockTransport(knowledge)
            )
        ),
        normative_protocol_writer=gateway.writer,
    )
    items = [
        {
            "page_number": n,
            "extracted_text": f"Учебный лист {n}",
            "page_facts": asdict(page_facts()),
            "normative_sources": [],
            "image_base64": _image(),
        }
        for n in range(1, 32)
    ]
    delays = []

    async def monitor():
        """Измеряет фактические задержки тестового event loop, без оценки общей RSS процесса."""
        previous = perf_counter()
        while True:
            await asyncio.sleep(0.01)
            current = perf_counter()
            delays.append(max(0, current - previous - 0.01) * 1000)
            previous = current

    heartbeat = asyncio.create_task(monitor())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://analysis.test"
    ) as client:
        response = await client.post(
            "/internal/v1/stages/check-norms",
            json={
                "document_id": str(UUID(int=900)),
                "normative_corpus": {
                    "section_id": SECTION,
                    "document_ids": [str(UUID(int=700 + n)) for n in range(11)],
                },
                "items": items,
            },
        )
    heartbeat.cancel()
    await asyncio.gather(heartbeat, return_exceptions=True)
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert [item["page_number"] for item in body["items"]] == list(range(1, 32))
    assert len(response.content) <= 8 * 1024**2
    plan = body["items"][0]["result"]["metrics"]["independent_normative"][
        "requirement_plan"
    ]
    assert plan["check_count"] == 10412
    assert "checks" not in plan and "selection" not in plan
    assert plan["protocol"]["counts"]["checks"] == 10412
    assert model.call_count == 31
    assert (
        len([path for path in knowledge_calls if path.endswith("/requirements")]) == 55
    )
    measurements = {
        "http_bytes": len(response.content),
        "http_MiB": len(response.content) / 1024**2,
        "archive_bytes": plan["protocol"]["bytes"],
        "records": plan["check_count"],
        "event_loop_max_lag_ms": max(delays, default=0),
    }
    (gateway.root / "serialization-measurements.json").write_bytes(
        canonical_json(measurements)
    )
    print(json.dumps(measurements))
    restored = await collect(
        gateway.store, UUID(int=900), plan["protocol"]["protocol_id"], "checks"
    )
    assert len(restored) == 10412
    assert all(row["source_ids"] for row in restored)
    expected_sources = {
        row["requirement_id"]: {**row, "section_id": SECTION}
        for document in documents.values()
        for row in document.requirements
    }
    assert {row["source_ids"][0] for row in restored} == set(expected_sources)
    assert all(
        row["source_reference"] == expected_sources[row["source_ids"][0]]
        for row in restored
    )
    assert plan["new_vlm_calls"] == plan["new_embedding_calls"] == 0


async def test_partial_write_corruption_and_unpublished_protocol_are_fail_closed(
    gateway,
):
    """Отсутствующий хвост и повреждённый digest не становятся ready даже после рестарта."""
    data = archive(await corpus_result(count=12))
    raw = canonical_json(data)
    protocol_id = hashlib.sha256(raw).hexdigest()
    await gateway.store.put_part(UUID(int=900), protocol_id, 0, raw[:100])
    with pytest.raises(NormativeProtocolNotFound):
        await gateway.store.read(UUID(int=900), protocol_id)
    with pytest.raises(NormativeProtocolError):
        await gateway.store.publish(
            UUID(int=900), protocol_id, {"part_count": 2, "bytes": len(raw)}
        )
    with pytest.raises(NormativeProtocolError):
        await gateway.store.put_part(UUID(int=900), protocol_id, 0, b"changed")


async def test_large_nested_objects_and_literal_ref_keys_round_trip(gateway):
    """Служебные ссылки не интерпретируют исходный ключ $ref; вложенные исключения не теряются."""
    result = await corpus_result(count=2)
    extension = {
        "$ref": "буквальный исходный ключ",
        "conditions": [
            {
                "if": {"scope": ["mode", "place"], "value": n},
                "exceptions": ["Исходная цитата " * 100],
            }
            for n in range(100)
        ],
    }
    result.diagnostics["checks"][0]["source_reference"]["extensions"] = extension
    ref = await gateway.writer.save(
        archive=archive(result), check_cancellation=no_cancel
    )
    rows = await collect(gateway.store, UUID(int=900), ref["protocol_id"], "checks")
    assert rows[0]["source_reference"]["extensions"] == extension
    assert (
        rows[0]["source_reference"]["document_id"]
        != rows[1]["source_reference"]["document_id"]
    )


async def test_cancelled_job_and_changed_scope_do_not_publish(gateway):
    """Чужой раздел и отменённое задание отвергаются на настоящем HTTP publish."""
    data = archive(await corpus_result(count=1))
    wrong = {**data, "section_id": str(uuid4())}
    with pytest.raises(NormativeProtocolUnavailable):
        await gateway.writer.save(archive=wrong, check_cancellation=no_cancel)
    gateway.jobs.job = replace(gateway.jobs.job, status=AnalysisJobStatus.CANCELLED)
    with pytest.raises(AnalysisCancelledError):
        await gateway.writer.save(archive=data, check_cancellation=no_cancel)


async def test_public_protocol_requires_job_access_and_never_accepts_document_uuid(
    gateway,
):
    """Знание UUID документа или digest не заменяет право владельца/guest capability."""
    ref = await gateway.writer.save(
        archive=archive(await corpus_result(count=3)), check_cancellation=no_cancel
    )
    job = gateway.jobs.job
    path = f"/api/v1/analyses/{job.id}/normative-protocols/{ref['protocol_id']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://gateway.test"
    ) as client:
        assert (await client.get(path)).status_code == 404
        headers = {"X-PDRD-Analysis-Access": job.guest_access_token}
        response = await client.get(
            path, params={"collection": "checks", "limit": 1}, headers=headers
        )
        assert response.status_code == 200 and response.json()["total"] == 3
        assert (
            await client.get(
                path.replace(str(job.id), str(job.document_id)), headers=headers
            )
        ).status_code == 404
        assert (
            await client.get(
                path.replace(ref["protocol_id"], "f" * 64), headers=headers
            )
        ).status_code == 404
        assert (
            await client.get(path, params={"limit": 201}, headers=headers)
        ).status_code == 422


@pytest.mark.parametrize("stage", ["norms", "finalize", "consistency"])
async def test_large_mandatory_findings_use_bounded_http_and_actual_workflow_restore(
    gateway, monkeypatch, stage
):
    """Нагрузочный transport mock: не обрезает findings и не переносит дефект в следующий HTTP-узел."""
    import pdrd_analysis_service.transport.http.document_context_routes as context_routes
    import pdrd_analysis_service.transport.http.stage_batch_routes as stage_routes
    from pdrd_analysis_service.transport.http.routes import _finding_draft_payload
    from pdrd_analysis_service.transport.http.schemas import (
        CheckNormsResponse,
        FinalFindingPayload,
        FinalizeResponse,
    )

    from ..unit.test_analysis_use_cases import finding

    app = _build_app(
        model=FakeVisionModel([]),
        progress_probe=RecordingProgressProbe(),
        concurrency=4,
    )
    app.state.container = replace(
        app.state.container, normative_protocol_writer=gateway.writer
    )
    expected = []
    for number in range(1, 32):
        draft = _finding_draft_payload(
            replace(
                finding(),
                page=number,
                finding_id=f"p{number}-f1",
                comment="Учебный большой proof record. " * 12000,
            )
        )
        if stage == "finalize":
            result = FinalizeResponse(
                summary="Проверено",
                findings=[
                    FinalFindingPayload.model_validate(
                        {
                            **draft.model_dump(mode="json"),
                            "recommendation": draft.recommendation_draft,
                            "experience_sources": [],
                        }
                    )
                ],
                metrics={},
            )
        else:
            result = CheckNormsResponse(
                summary="Проверено", findings=[draft], metrics={}
            )
        expected.append(
            {"page_number": number, "result": result.model_dump(mode="json")}
        )

    async def check_page(*, request, **kwargs):
        """Заменяет только проверку страницы, проверяем реальные stage/DTO/upload/workflow."""
        return CheckNormsResponse.model_validate(
            expected[request.page_number - 1]["result"]
        )

    async def final_page(*, request, **kwargs):
        """Номер физической страницы берётся из исходного finding, без потери ID."""
        number = request.findings[0].page
        return FinalizeResponse.model_validate(expected[number - 1]["result"])

    monkeypatch.setattr(stage_routes, "check_norms_controlled", check_page)
    monkeypatch.setattr(stage_routes, "finalize_findings", final_page)
    monkeypatch.setattr(
        context_routes,
        "_checked_response",
        lambda *args, **kwargs: context_routes.CheckConsistencyResponse(
            findings=[], items=expected
        ),
    )
    scope = {
        "section_id": SECTION,
        "document_ids": [str(UUID(int=700 + n)) for n in range(11)],
    }
    items = [
        {
            "page_number": n,
            "extracted_text": f"Лист {n}",
            "page_facts": asdict(page_facts()),
            "normative_sources": [],
            "image_base64": _image(),
        }
        for n in range(1, 32)
    ]
    if stage == "norms":
        path, body, source = (
            "/internal/v1/stages/check-norms",
            {"items": items},
            "Check Norms",
        )
    elif stage == "finalize":
        final_items = [
            {
                "page_number": n,
                "request": {
                    "findings": [
                        _finding_draft_payload(replace(finding(), page=n)).model_dump(
                            mode="json"
                        )
                    ],
                    "experience_by_finding": {},
                },
            }
            for n in range(1, 32)
        ]
        path, body, source = (
            "/internal/v1/stages/finalize",
            {"items": final_items},
            "Finalize Findings",
        )
    else:
        pages = [
            {key: item[key] for key in ("page_number", "extracted_text", "page_facts")}
            for item in items
        ]
        path, body, source = (
            "/internal/v1/document-context/check-consistency",
            {"pages": pages, "enabled": False},
            "Check Cross-Page Consistency",
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://analysis.test"
    ) as client:
        response = await client.post(
            path,
            json={"document_id": str(UUID(int=900)), "normative_corpus": scope, **body},
        )
    assert response.status_code == 200, response.text[:500]
    data = response.json()
    assert len(response.content) < 8 * 1024**2
    assert (
        data["items"] == []
        and data["results_reference"]["counts"]["stage_records"] >= 62
    )
    ref, batches, offset = data["results_reference"], [], 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway.app), base_url="http://gateway.test"
    ) as client:
        while True:
            page = await client.get(
                f"/internal/v1/analysis-artifacts/{UUID(int=900)}/normative-protocols/{ref['protocol_id']}/stage-stream",
                params={"offset": offset},
            )
            assert page.status_code == 200, page.text[:200]
            assert len(page.content) < 360000
            batches.append(page.json())
            if page.json()["next_offset"] is None:
                break
            offset = page.json()["next_offset"]
    restored = run_resolver(source, data, batches)
    # Штатный fallback добавляет только компактное уведомление об отсутствии корпуса.
    if stage == "norms":
        expected[0]["result"]["metrics"]["independent_normative"] = {
            "mode": "review_not_configured",
            "corpus_received": True,
            "corpus_state": "not_prepared",
            "requirements": 0,
            "source_complete": False,
            "normative_coverage": "incomplete",
            "normative_atomic_check": "unavailable",
            "reason": "Независимая проверка корпуса не настроена в сервисе.",
        }
    assert restored["pages"] == list(range(1, 32)) and restored["findings"] == 31
    assert restored["digest"] == hashlib.sha256(canonical_json(expected)).hexdigest()
    with pytest.raises(subprocess.CalledProcessError):
        run_resolver(source, data, batches[:-1])


@pytest.mark.parametrize("lost_ack", ["part", "publish"])
async def test_lost_ack_retries_only_idempotent_protocol_operation(gateway, lost_ack):
    """Обрыв после записи не создаёт другой архив и не запускает заново нормативный анализ."""
    delegate = httpx.ASGITransport(app=gateway.app)
    calls = []

    async def transport(request):
        """Сохраняет запись настоящим HTTP handler, затем теряет только её подтверждение."""
        response = await delegate.handle_async_request(request)
        key = "publish" if request.url.path.endswith("/publish") else "part"
        calls.append(key)
        if key == lost_ack and calls.count(key) == 1:
            raise httpx.ReadError("Учебная потеря ACK", request=request)
        return response

    writer = replace(gateway.writer, transport=httpx.MockTransport(transport))
    ref = await writer.save(
        archive=archive(await corpus_result(count=2)), check_cancellation=no_cancel
    )
    assert calls.count(lost_ack) == 2
    await gateway.store.verify(UUID(int=900), ref["protocol_id"])


async def test_retry_is_bounded_and_cancellation_does_not_publish(gateway):
    """Бесконечных retry нет; отмена между порциями оставляет только неопубликованные части."""
    calls = []

    def failed(request):
        """Имитирует только сетевую недоступность, а не программное исключение."""
        calls.append(request)
        raise httpx.ConnectError("Учебная недоступность", request=request)

    data = archive(await corpus_result(count=100))
    with pytest.raises(NormativeProtocolUnavailable):
        await replace(gateway.writer, transport=httpx.MockTransport(failed)).save(
            archive=data, check_cancellation=no_cancel
        )
    assert len(calls) == 3
    checkpoints = 0

    async def cancel():
        """Отменяет после подтверждения первой сохранённой части."""
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 3:
            raise AnalysisCancelledError("Учебная отмена")

    with pytest.raises(AnalysisCancelledError):
        await gateway.writer.save(archive=data, check_cancellation=cancel)
    protocol_id = hashlib.sha256(canonical_json(data)).hexdigest()
    with pytest.raises(NormativeProtocolNotFound):
        await gateway.store.read(UUID(int=900), protocol_id)


async def test_ingestion_rejects_missing_or_tampered_protocol(gateway):
    """Сохранённый result не принимается по одному наличию manifest без исходных частей."""
    ref = await gateway.writer.save(
        archive=archive(await corpus_result(count=2)), check_cancellation=no_cancel
    )

    def result(reference):
        """Ссылка находится только в штатной позиции page metrics."""
        return {
            "metrics": {
                "independent_normative": {"requirement_plan": {"protocol": reference}}
            }
        }

    await gateway.manager.validate_result(
        gateway.jobs.job.id, UUID(int=900), result(ref)
    )
    with pytest.raises(NormativeProtocolError):
        await gateway.manager.validate_result(
            gateway.jobs.job.id,
            UUID(int=900),
            result({**ref, "document_id": str(uuid4())}),
        )
    with pytest.raises(NormativeProtocolError):
        await gateway.manager.validate_result(
            gateway.jobs.job.id, UUID(int=900), result({**ref, "counts": {"checks": 0}})
        )
    (
        gateway.store._directory(UUID(int=900), ref["protocol_id"]) / "000000.part"
    ).unlink()
    with pytest.raises(NormativeProtocolError):
        await gateway.manager.validate_result(
            gateway.jobs.job.id, UUID(int=900), result(ref)
        )


async def test_h09_h10_h16_h24_keep_status_and_proofs_through_n_first_finalization_and_storage(
    gateway,
):
    """Исходные страницы и геометрические факты проходят алгоритм, без готовых эталонных findings."""
    from pdrd_analysis_service.application.use_cases.document_context import (
        CheckCrossPageConsistency,
    )
    from pdrd_analysis_service.application.use_cases.finalization import (
        FinalizeFindings,
    )
    from pdrd_analysis_service.transport.http.document_context_routes import _options

    from .test_quality_stage3_http import NoDecisions, control_pages

    pages = control_pages()
    model = FakeVisionModel([{"summary": "Проверено", "violations": []} for _ in pages])
    app = _build_app(
        model=model, progress_probe=RecordingProgressProbe(), concurrency=4
    )
    decision_model = NoDecisions()
    app.state.container = replace(
        app.state.container,
        normative_protocol_writer=gateway.writer,
        independent_normative_review=IndependentNormativeReview(CorpusReader(count=12)),
        check_cross_page_consistency=CheckCrossPageConsistency(
            decision_model, _options(app.state.container)
        ),
        finalize_findings=FinalizeFindings(decision_model, 4000, 8, 1000, 0.5),
    )
    scope = {
        "section_id": SECTION,
        "document_ids": [str(UUID(int=700 + n)) for n in range(11)],
    }
    document_id = str(UUID(int=900))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://analysis.test"
    ) as client:
        checked = await client.post(
            "/internal/v1/stages/check-norms",
            json={
                "document_id": document_id,
                "normative_corpus": scope,
                "items": [
                    {
                        "page_number": p["page_number"],
                        "extracted_text": p["extracted_text"],
                        "page_facts": p["page_facts"],
                        "normative_sources": [],
                        "image_base64": _image(),
                    }
                    for p in pages
                ],
            },
        )
        assert checked.status_code == 200, checked.text[:500]
        cross = await client.post(
            "/internal/v1/document-context/check-consistency",
            json={
                "document_id": document_id,
                "normative_corpus": scope,
                "pages": pages,
                "page_checks": checked.json()["items"],
                "enabled": True,
            },
        )
        assert cross.status_code == 200, cross.text[:500]
        before = {
            f["finding_id"]: f
            for item in cross.json()["items"]
            for f in item["result"]["findings"]
        }
        final = await client.post(
            "/internal/v1/stages/finalize",
            json={
                "document_id": document_id,
                "normative_corpus": scope,
                "items": [
                    {
                        "page_number": item["page_number"],
                        "request": {
                            "findings": item["result"]["findings"],
                            "experience_by_finding": {},
                        },
                    }
                    for item in cross.json()["items"]
                ],
            },
        )
        assert final.status_code == 200, final.text[:500]
    findings = [f for item in final.json()["items"] for f in item["result"]["findings"]]
    selected = {
        "H09": next(
            f
            for f in findings
            if "водоотведение" in f["comment"] and "водоснабжение" in f["comment"]
        ),
        "H10": next(
            f for f in findings if "количество поставляемой трубы" in f["comment"]
        ),
        "H16": next(
            f for f in findings if "0,5205" in f["comment"] and "0,205" in f["comment"]
        ),
        "H24": next(
            f
            for f in findings
            if "таблица 2" in f["comment"] and "таблица 3" in f["comment"]
        ),
    }
    assert selected["H10"]["status"] == selected["H16"]["status"] == "needs_review"
    for found in selected.values():
        original = before[found["finding_id"]]
        assert found["status"] == original["status"]
        for field in ("evidence_locations", "visual_regions", "object_ref"):
            assert found[field] == original[field]

        def proof_records(value):
            """Отделяет исходные факты от дополняемой истории решений Finalization."""
            records = set()
            if isinstance(value, dict):
                if "fact" in value and "page" in value:
                    records.add(hashlib.sha256(canonical_json(value)).hexdigest())
                for child in value.values():
                    records.update(proof_records(child))
            elif isinstance(value, list):
                for child in value:
                    records.update(proof_records(child))
            return records

        proofs = proof_records(original["origin_assertions"])
        assert proofs and proofs <= proof_records(found["origin_assertions"])
    store = gateway.artifacts
    result = {
        "pages": [
            {"page_number": item["page_number"], "normative_check": item["result"]}
            for item in checked.json()["items"]
        ],
        "findings": findings,
    }
    await gateway.manager.validate_result(
        gateway.jobs.job.id, UUID(document_id), result
    )
    await store.save_result(document_id=UUID(document_id), result=result)
    assert (await store.load_result(document_id=UUID(document_id)))[
        "findings"
    ] == findings
    print(
        json.dumps(
            {
                case: {
                    "status": finding["status"],
                    "pages": sorted({r["page"] for r in finding["evidence_locations"]}),
                }
                for case, finding in selected.items()
            }
        )
    )


@pytest.mark.parametrize("mode", ["cad", "pdf-cad"])
@pytest.mark.parametrize("stage", ["norms", "finalize"])
async def test_large_single_sheet_contract_and_published_cad_resolver(
    gateway, monkeypatch, mode, stage
):
    """Одиночные CAD HTTP-операции имеют тот же бюджет и lossless-передачу findings."""
    import pdrd_analysis_service.transport.http.routes as routes
    from pdrd_analysis_service.domain.analysis import FinalFinding
    from pdrd_analysis_service.transport.http.schemas import CheckNormsResponse

    from ..unit.test_analysis_use_cases import finding

    app = _build_app(
        model=FakeVisionModel([]),
        progress_probe=RecordingProgressProbe(),
        concurrency=1,
    )
    original = replace(finding(), comment="Обязательное доказательство. " * 200000)
    draft = routes._finding_draft_payload(original)
    app.state.container = replace(
        app.state.container, normative_protocol_writer=gateway.writer
    )
    scope = {
        "section_id": SECTION,
        "document_ids": [str(UUID(int=700 + n)) for n in range(11)],
    }
    body = {"document_id": str(UUID(int=900)), "normative_corpus": scope}
    if stage == "norms":
        expected = CheckNormsResponse(
            summary="Сохранено", findings=[draft], metrics={}
        ).model_dump(mode="json")

        async def checked(**kwargs):
            """Изолирует только инженерную генерацию от проверки transport boundary."""
            return CheckNormsResponse.model_validate(expected)

        monkeypatch.setattr(routes, "check_norms_controlled", checked)
        body.update(
            page_number=1,
            extracted_text="Лист",
            page_facts=asdict(page_facts()),
            normative_sources=[],
            image_base64=_image(),
        )
        path, source = "/internal/v1/pages/check-norms", "Check Norms"
    else:
        final = FinalFinding(
            **{
                key: getattr(original, key)
                for key in (
                    "finding_id",
                    "page",
                    "page_type",
                    "category",
                    "severity",
                    "status",
                    "comment",
                    "evidence",
                    "confidence",
                    "basis",
                    "basis_sources",
                )
            },
            recommendation=original.recommendation_draft,
            experience_sources=(),
        )

        async def finalized(**kwargs):
            """Возвращает полноценную domain-находку, не отключает HTTP сериализацию или сохранение."""
            return "Сохранено", (final,), {}

        app.state.container = replace(
            app.state.container, finalize_findings=SimpleNamespace(execute=finalized)
        )
        expected = routes.FinalizeResponse(
            summary="Сохранено",
            findings=[routes._final_finding_payload(final, visual_regions=())],
            metrics={},
        ).model_dump(mode="json")
        body.update(
            findings=[routes._finding_draft_payload(finding()).model_dump(mode="json")],
            experience_by_finding={},
        )
        path, source = "/internal/v1/findings/finalize", "Finalize Findings"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://analysis.test"
    ) as client:
        response = await client.post(path, json=body)
    assert response.status_code == 200, response.text[:200]
    assert len(response.content) < 8 * 1024**2
    data, batches, offset = response.json(), [], 0
    ref = data["results_reference"]
    while True:
        part = await gateway.store.stream(UUID(int=900), ref["protocol_id"], offset)
        batches.append(part)
        if part["next_offset"] is None:
            break
        offset = part["next_offset"]
    restored = run_resolver(source, data, batches, mode)
    assert restored["findings"] == 1 and restored["pages"] == [1]
    assert (
        restored["digest"]
        == hashlib.sha256(
            canonical_json([{"page_number": 1, "result": expected}])
        ).hexdigest()
    )
