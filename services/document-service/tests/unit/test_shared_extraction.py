# services/document-service/tests/unit/test_shared_extraction.py

"""Регрессии REST-интеграций, сохранности строк и выборочного OCR без рабочих сервисов."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import fitz
import httpx
import pytest
from pdrd_document_service.application.ports.specification_extraction import (
    ExtractionArtifactStorageError,
    SpecificationExtractionError,
)
from pdrd_document_service.application.use_cases.enrich_document import EnrichDocument
from pdrd_document_service.core.settings import (
    DoclingSettings,
    SpecificationServiceSettings,
)
from pdrd_document_service.domain.external_specification import (
    bind_external_specification,
)
from pdrd_document_service.domain.pdf import PdfDocument, PdfPage, PdfPageType
from pdrd_document_service.infrastructure.docling import HttpDoclingConverter
from pdrd_document_service.infrastructure.extraction_artifacts import (
    FilesystemExtractionArtifacts,
)
from pdrd_document_service.infrastructure.specification_extraction import (
    HttpSpecificationExtractor,
)
from pydantic import SecretStr, ValidationError


async def test_storage_probe_preserves_cached_results_and_leaves_no_files(tmp_path):
    """Проверка готовности не меняет digest и не оставляет мусор в собственном томе."""
    store = FilesystemExtractionArtifacts(tmp_path / "new" / "docling")
    await store.check_ready()
    await store.save("a" * 64, {"texts": ["исходное чтение"], "physical_page": 26})
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    await store.check_ready()
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before
    assert (await store.load("a" * 64))["physical_page"] == 26


async def test_storage_io_failure_is_explicit_but_unexpected_errors_are_not_hidden(
    tmp_path, monkeypatch
):
    """EACCES типизирован, повреждённый digest/программные исключения не подменяются пустым кэшем."""
    from pdrd_document_service.infrastructure import extraction_artifacts

    store = FilesystemExtractionArtifacts(tmp_path)

    def denied(*args, **kwargs):
        """Воспроизводит отказ ядра при сохранении результата."""
        raise PermissionError(13, "test-only")

    monkeypatch.setattr(extraction_artifacts.tempfile, "mkstemp", denied)
    with pytest.raises(ExtractionArtifactStorageError) as caught:
        await store.save("a" * 64, {})
    assert isinstance(caught.value.__cause__, PermissionError)
    assert not list(tmp_path.iterdir())
    (tmp_path / ("a" * 64 + ".json")).write_text(
        json.dumps({"payload": "{}", "sha256": "bad"}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="Повреждён"):
        await store.load("a" * 64)


def test_spec_default_matches_existing_service_port():
    """Контейнер обращается к порту API 8083, а не к отсутствующему 8080."""
    assert (
        SpecificationServiceSettings().base_url
        == "http://spec-extracting-ai-api-1:8083"
    )


def item(caption="Труба ПЭ100 SDR11 110x10", page="26"):
    """Исходный внешний экспорт с намеренно пустыми Brand и Article."""
    return {
        "Caption": caption,
        "Units": "м",
        "Quantity": "150",
        "UserComment1": "ПРОЕКТ",
        "Pages": page,
        "Brand": "",
        "Article": "",
        "Note": "",
    }


def pdf():
    """Создаёт PDF для реального выделения выбранной страницы в адаптере."""
    with fitz.open() as document:
        document.new_page().insert_text((20, 30), "ONE")
        document.new_page().insert_text((20, 30), "TWO")
        return document.tobytes()


@pytest.mark.parametrize("count", [1, 20, 100])
async def test_spec_retries_status_materializes_all_rows_and_survives_external_ttl(
    tmp_path, count
):
    """503 повторяет чтение; JSON, пустые поля и warnings доступны после удаления внешнего задания."""
    jid = str(uuid4())
    calls, external_rows = [], [item(f"Строка {i}") for i in range(count)]
    polls = 0

    async def handler(request):
        """Моделирует опубликованный API с первой временной ошибкой чтения."""
        nonlocal polls
        calls.append((request.method, request.url.path))
        assert request.headers["X-API-Key"] == "dedicated-test-key"
        if request.method == "POST":
            assert len(request.headers["Idempotency-Key"]) == 64
            return httpx.Response(202, json={"job_id": jid, "status": "queued"})
        if request.url.path.endswith("result.json"):
            return httpx.Response(
                200, json={"file_name": "source.pdf", "items": external_rows}
            )
        if request.url.path.endswith("warnings"):
            return httpx.Response(
                200, json={"total": 1, "warnings": [{"code": "geometry_unknown"}]}
            )
        polls += 1
        if polls == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json={
                "job_id": jid,
                "status": "completed_with_warnings",
                "processed_pages": 1,
                "selected_pdf_pages": [26],
                "item_count": count,
                "prompt_version": "one",
            },
        )

    settings = SpecificationServiceSettings(
        enabled=True,
        api_key=SecretStr("dedicated-test-key"),
        poll_interval_seconds=0.001,
    )
    store = FilesystemExtractionArtifacts(tmp_path)
    client = HttpSpecificationExtractor(
        settings=settings, artifacts=store, transport=httpx.MockTransport(handler)
    )
    first = await client.extract(
        content=b"original", file_name="source.pdf", pages=(26,), project_code="ПРОЕКТ"
    )
    assert first.result["items"] == external_rows
    assert first.status == "completed_with_warnings" and first.warnings
    assert polls == 2 and sum(m == "POST" for m, p in calls) == 1
    before = list(calls)
    second = await client.extract(
        content=b"original", file_name="source.pdf", pages=(26,), project_code="ПРОЕКТ"
    )
    assert first == second and calls == before


async def test_spec_incomplete_result_cannot_be_published(tmp_path):
    """Другие страницы и потерянные строки отклоняются до записи готового кэша."""
    jid = str(uuid4())

    async def handler(request):
        """Возвращает неверное число обработанных физических страниц."""
        return httpx.Response(
            202,
            json={
                "job_id": jid,
                "status": "completed",
                "selected_pdf_pages": [26],
                "processed_pages": 0,
            },
        )

    client = HttpSpecificationExtractor(
        settings=SpecificationServiceSettings(),
        artifacts=FilesystemExtractionArtifacts(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SpecificationExtractionError, match="страниц"):
        await client.extract(
            content=b"source", file_name="s.pdf", pages=(26,), project_code="P"
        )
    assert not list(tmp_path.glob("*.json"))


async def test_spec_cancellation_requests_external_cancel_and_keeps_no_ready_cache(
    tmp_path,
):
    """Отмена ожидания не оставляет неподтверждённый результат или бесконтрольное внешнее задание."""
    jid, started, cancelled = str(uuid4()), asyncio.Event(), []

    async def handler(request):
        """Ожидает явную отмену клиента в стадии очереди."""
        if request.url.path.endswith("cancel"):
            cancelled.append(True)
        else:
            started.set()
        return httpx.Response(202, json={"job_id": jid, "status": "queued"})

    client = HttpSpecificationExtractor(
        settings=SpecificationServiceSettings(),
        artifacts=FilesystemExtractionArtifacts(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    task = asyncio.create_task(
        client.extract(
            content=b"source", file_name="s.pdf", pages=(26,), project_code="P"
        )
    )
    await started.wait()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled and not list(tmp_path.glob("*.json"))


def test_unknown_geometry_and_duplicate_names_keep_every_independent_original_row():
    """Две одинаковые позиции сохраняются раздельно без вымышленных рамок и модели."""
    originals = [item(), item(), item("Резервуар V=50м3")]
    result = bind_external_specification(
        page_number=26,
        native=None,
        extraction={"items": originals},
        job_id=str(uuid4()),
        artifact_key="a" * 64,
        warnings=(),
    )
    assert len(result["items"]) == result["raw_row_count"] == 3
    assert len({i["item_id"] for i in result["items"]}) == 3
    assert all(
        i["geometry_status"] == "geometry_unresolved" and not i["source_regions"]
        for i in result["items"]
    )
    assert [i["external_original"] for i in result["items"]] == originals
    assert all(i["model"] == "" and i["article"] == "" for i in result["items"])


def test_bound_native_fields_are_preserved_but_different_variant_cannot_claim_geometry():
    """Пустой Brand экспорта не стирает доказанный столбец PDF; иное исполнение не получает чужой bbox."""
    original = item()
    region = {
        "page": 26,
        "row_id": "native-1",
        "x_min": 10,
        "y_min": 10,
        "x_max": 50,
        "y_max": 50,
    }
    parameter = {
        "property_name": "pressure",
        "value_raw": "10",
        "unit_raw": "бар",
        "evidence_text": "PN10",
        "evidence_status": "confirmed",
    }
    native = {
        "items": [
            {
                **original,
                "item_id": "native-1",
                "source_pages": [26],
                "source_regions": [region],
                "model": "ABC-1",
                "article": "XYZ/2",
                "manufacturer": "Завод",
                "under": "В1",
                "parameters": [parameter],
            }
        ]
    }

    def bind(row):
        """Связывает одну авторитетную позицию с неизменным исходным PDF."""
        return bind_external_specification(
            page_number=26,
            native=native,
            extraction={"items": [row]},
            job_id=str(uuid4()),
            artifact_key="a" * 64,
            warnings=(),
        )["items"][0]

    result = bind(original)
    assert result["geometry_status"] == "resolved" and result["under"] == "В1"
    assert result["model"] == "ABC-1" and result["article"] == "XYZ/2"
    assert parameter in result["parameters"]
    assert bind({**original, "Quantity": "150.0"})["geometry_status"] == "resolved"
    assert bind({**original, "Quantity": "150,0"})["geometry_status"] == "resolved"
    assert (
        bind({**original, "Quantity": "1500"})["geometry_status"]
        == "geometry_unresolved"
    )
    assert (
        result["external_original"] == original
        and result["Brand"] == result["Article"] == ""
    )
    assert (
        bind({**original, "Brand": "ABC-2"})["geometry_status"] == "geometry_unresolved"
    )
    assert (
        bind({**original, "Article": "XYZ/3"})["geometry_status"]
        == "geometry_unresolved"
    )


async def test_docling_sends_only_selected_page_and_never_forces_ocr(tmp_path):
    """Реальный PyMuPDF выделяет второй лист; API получает единственный файл и верные OCR-флаги."""
    requests = []

    async def handler(request):
        """Воспроизводит фактический асинхронный контракт Docling 1.38.2."""
        requests.append(request)
        if request.method == "POST":
            raw = await request.aread()
            assert b'name="force_ocr"\r\n\r\nfalse' in raw
            assert b'name="do_ocr"\r\n\r\nfalse' in raw
            assert b'name="ocr_lang"\r\n\r\nrus' in raw
            assert b'name="ocr_lang"\r\n\r\neng' in raw
            start = raw.index(b"%PDF-")
            end = raw.index(b"%%EOF", start) + 5
            with fitz.open(stream=raw[start:end]) as d:
                assert (
                    len(d) == 1
                    and "TWO" in d[0].get_text()
                    and "ONE" not in d[0].get_text()
                )
            return httpx.Response(
                200, json={"task_id": "test-task", "task_status": "success"}
            )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "document": {
                    "json_content": {"texts": [{"text": "Таблица"}], "tables": []}
                },
            },
        )

    client = HttpDoclingConverter(
        settings=DoclingSettings(),
        artifacts=FilesystemExtractionArtifacts(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    first = await client.convert(content=pdf(), page_number=2, do_ocr=False)
    assert first["physical_page"] == 2 and first["converted_page"] == 1
    assert len(requests) == 2


async def test_route_keeps_good_native_text_and_skips_spec_already_read_externally():
    """OCR применяется только к скану; надёжный текст и готовый состав не читаются второй раз."""

    class Converter:
        """Регистрирует действительно выбранные страницы, а не весь PDF."""

        def __init__(self):
            """Инициализирует наблюдения вызовов."""
            self.pages = []

        async def convert(self, **kwargs):
            """Возвращает текст одного скана без изменения его физического номера."""
            self.pages.append((kwargs["page_number"], kwargs["do_ocr"]))
            return {
                "artifact_key": "b" * 64,
                "result": {"document": {"json_content": {"texts": [{"text": "Скан"}]}}},
            }

    converter = Converter()
    good = PdfPage(1, PdfPageType.GENERAL_NOTES, "Надёжный текст", 100, 200, b"image")
    scanned = replace(good, number=2, text="")
    spec = replace(
        good,
        number=3,
        page_type=PdfPageType.SPECIFICATION,
        equipment_specification={
            "external_extraction": {"provider": "spec_extracting_ai"}
        },
    )
    result = await EnrichDocument(converter).execute(
        document=PdfDocument(3, (good, scanned, spec)), content=b"source"
    )
    assert converter.pages == [(2, True)]
    assert result.pages[0] == good and result.pages[2] == spec
    assert (
        result.pages[1].text.endswith("\nСкан")
        and "[DOCLING_OCR_ALTERNATIVE:" in result.pages[1].text
        and result.pages[1].conversion_metadata["native_text_preserved"] is False
    )


def test_enabled_integrations_require_dedicated_keys():
    """Пустой ключ не заменяется чужим административным секретом."""
    for settings in (SpecificationServiceSettings, DoclingSettings):
        with pytest.raises(ValidationError):
            settings(enabled=True)


def test_public_env_aliases_reach_nested_clients_without_exposing_keys(
    tmp_path, monkeypatch
):
    """Публичные переменные владельца читаются до проверки включённых вложенных настроек."""
    from pdrd_document_service.core.settings import Settings

    env = tmp_path / ".env"
    env.write_text(
        "SPEC_EXTRACTING_AI_BASE_URL=http://127.0.0.1:8083\n"
        "SPEC_EXTRACTING_AI_API_KEY=spec-unit-secret\n"
        "DOCLING_BASE_URL=http://docling-eval:5001\n"
        "DOCLING_API_KEY=docling-unit-secret\n"
        "DOCUMENT_SERVICE_SPECIFICATION_SERVICE__ENABLED=true\n"
        "DOCUMENT_SERVICE_DOCLING__ENABLED=true\n",
        encoding="utf-8",
    )
    # Окружение процесса имеет приоритет над .env для того же публичного имени.
    monkeypatch.setenv("SPEC_EXTRACTING_AI_BASE_URL", "http://provider.test:8083")
    settings = Settings(_env_file=env)
    assert settings.specification_service.base_url == "http://provider.test:8083"
    assert (
        settings.specification_service.api_key.get_secret_value() == "spec-unit-secret"
    )
    assert settings.docling.base_url == "http://docling-eval:5001"
    assert settings.docling.api_key.get_secret_value() == "docling-unit-secret"
    assert "spec-unit-secret" not in repr(settings)
    assert "docling-unit-secret" not in repr(settings)


async def test_artifact_digest_and_path_are_verified(tmp_path):
    """Нельзя подменить собственный сохранённый результат или выйти из каталога."""
    store = FilesystemExtractionArtifacts(tmp_path)
    with pytest.raises(ValueError):
        await store.load("../foreign")
    await store.save("a" * 64, {"items": [item()]})
    path = tmp_path / ("a" * 64 + ".json")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["payload"] = "{}"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Повреждён"):
        await store.load("a" * 64)


async def test_failed_operation_can_restart_but_transport_retry_keeps_operation_key(
    tmp_path,
):
    """После failed новая попытка не привязывается навсегда к старому внешнему заданию."""
    keys = []

    async def handler(request):
        """Первый HTTP-сбой повторяется идемпотентно, failed завершается явно."""
        keys.append(request.headers["Idempotency-Key"])
        if len(keys) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(202, json={"job_id": str(uuid4()), "status": "failed"})

    client = HttpSpecificationExtractor(
        settings=SpecificationServiceSettings(),
        artifacts=FilesystemExtractionArtifacts(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    for _ in range(2):
        with pytest.raises(SpecificationExtractionError, match="failed"):
            await client.extract(
                content=b"source", file_name="s.pdf", pages=(26,), project_code="P"
            )
    assert keys[0] == keys[1] and keys[2] != keys[1]
    assert not list(tmp_path.glob("*.json"))


async def test_complex_table_reading_reaches_analysis_text_without_losing_native():
    """Полный текст ячеек участвует в последующем анализе; raw bbox остаётся в собственном артефакте."""

    class Converter:
        """Возвращает объединённую ячейку из реального контракта Docling."""

        async def convert(self, **kwargs):
            """Обычная таблица не вызывает OCR нативного текста."""
            assert kwargs["do_ocr"] is False
            return {
                "artifact_key": "a" * 64,
                "result": {
                    "document": {
                        "json_content": {
                            "texts": [{"text": "Альтернативное чтение"}],
                            "tables": [
                                {
                                    "data": {
                                        "table_cells": [
                                            {
                                                "text": "100 м3",
                                                "start_row_offset_idx": 0,
                                                "end_row_offset_idx": 2,
                                                "start_col_offset_idx": 1,
                                                "end_col_offset_idx": 2,
                                            }
                                        ]
                                    }
                                }
                            ],
                        }
                    }
                },
            }

    page = PdfPage(1, PdfPageType.TABLE, "Исходный текст", 100, 200, b"image")
    result = await EnrichDocument(Converter()).execute(
        document=PdfDocument(1, (page,)), content=b"pdf"
    )
    actual = result.pages[0]
    assert (
        actual.text.startswith(page.text)
        and "100 м3" in actual.text
        and "row_end=2" in actual.text
    )
    assert actual.conversion_metadata["native_text_preserved"] is True
    assert (
        "native_text" not in actual.conversion_metadata and not actual.alternative_text
    )
