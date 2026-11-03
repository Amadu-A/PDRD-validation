# services/equipment-search-service/tests/unit/test_discovery_assessment.py

"""Предметные отказы, неизвестные бренды, частичный поиск и безопасное обнаружение."""

from unittest.mock import AsyncMock

import httpx
import pytest
from pdrd_equipment_search_service.domain.discovery import discover_links, public_url
from pdrd_equipment_search_service.domain.equipment import (
    DownloadedDocument,
    EquipmentIdentity,
)
from pdrd_equipment_search_service.infrastructure.inspect_document import (
    DocumentServiceInspector,
)
from pdrd_equipment_search_service.infrastructure.searxng import (
    SearchUnavailable,
    SearxngSearch,
)


@pytest.mark.parametrize(
    ("model", "other"),
    [
        ("ВСКМ 90-20", "ВСКМ 90-40"),
        ("ШПК-320Н", "ШПК-310Н"),
        ("ВСХНд-50", "ВСХНд-80"),
        ("DRC-100B", "DRC-100A"),
        ("МКУ-24-8-К", "МКУ-24-9-К"),
    ],
)
async def test_explicit_unknown_manufacturer_is_not_a_seller_domain(model, other):
    """Точная подписанная модель подтверждается, соседняя маркировка отклоняется."""
    text = f"Паспорт\nМодель: {model}\nИзготовитель: ООО «Завод приборов»\nНоминальный расход: 2 м3/ч"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"pages": [{"page": 2, "text": text}]}
            )
        )
    ) as client:
        inspector = DocumentServiceInspector(client, "http://document")
        result = await inspector.inspect(
            EquipmentIdentity("", model, manufacturer_status="unknown"),
            DownloadedDocument(
                b"%PDF-test", "https://seller.example/file", "application/pdf"
            ),
        )
        assert result.applicable and "Завод приборов" in result.manufacturer
        assert result.manufacturer_evidence
        wrong = await inspector.inspect(
            EquipmentIdentity("", other, manufacturer_status="unknown"),
            DownloadedDocument(
                b"%PDF-test", "https://seller.example/file", "application/pdf"
            ),
        )
        assert not wrong.applicable and wrong.reason_code == "model_mismatch"


@pytest.mark.parametrize(
    "title,expected",
    [("Руководство по эксплуатации", True), ("Каталог продукции", False)],
)
async def test_unique_manufacturer_label_on_another_manual_page(title, expected):
    """Модель на стр. 8 и изготовитель на стр. 12 связываются только в одном руководстве."""
    pages = [
        {"page": 1, "text": title},
        {"page": 8, "text": "Модель ВСХНд-50"},
        {"page": 12, "text": "Изготовитель: АО «Тепловодомер»."},
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"pages": pages})
        )
    ) as client:
        result = await DocumentServiceInspector(client, "http://document").inspect(
            EquipmentIdentity("", "ВСХНд-50", manufacturer_status="unknown"),
            DownloadedDocument(
                b"%PDF-manual", "https://maker.example/doc", "application/pdf"
            ),
        )
    assert result.applicable is expected
    if expected:
        assert (
            result.manufacturer == "АО «Тепловодомер»"
            and result.manufacturer_evidence_page == 12
        )


async def test_targeted_vision_prefers_exact_model_page_to_a_generic_technical_cover():
    """Два адресных чтения не тратятся сначала на обложку без нужной модели."""
    from types import SimpleNamespace

    from pdrd_equipment_search_service.application.document_facts import (
        extract_document_facts,
    )
    from pdrd_equipment_search_service.application.vision_budget import (
        EquipmentVisionBudget,
    )
    from pdrd_equipment_search_service.domain.equipment import (
        DocumentInspection,
        DocumentSnapshot,
    )

    pages = (
        (1, "Технические характеристики семейства"),
        (8, "ВСХНд-50 Технические характеристики"),
    )
    inspection = DocumentInspection(True, pages=pages, vision_pages=(8,))
    fact = {"snippet": "ВСХНд-50 диаметр 50 мм", "page": 8}
    vision = SimpleNamespace(
        inspect=AsyncMock(return_value=DocumentInspection(True, vision_facts=(fact,)))
    )
    index = SimpleNamespace(
        extract_or_get=AsyncMock(side_effect=[("ready", ()), ("needs_review", (fact,))])
    )
    catalog = SimpleNamespace(manufacturer_aliases=AsyncMock(return_value=("Maker",)))
    snapshot = DocumentSnapshot(
        "EQ-test",
        "Maker",
        "ВСХНд-50",
        "",
        "https://maker.example/doc",
        "https://maker.example/doc",
        "a" * 64,
        "snapshot",
        "",
        "trusted",
    )
    await extract_document_facts(
        catalog=catalog,
        index=index,
        vision=vision,
        identity=EquipmentIdentity("Maker", "ВСХНд-50"),
        snapshot=snapshot,
        pages=pages,
        visual=(),
        budget=EquipmentVisionBudget(2),
        inspector=None,
        document=DownloadedDocument(
            b"%PDF-test", snapshot.final_url, "application/pdf"
        ),
        inspection=inspection,
    )
    assert vision.inspect.await_args.args[2].vision_pages[0] == 8


@pytest.mark.parametrize(
    "item_type,model,reference,text",
    [
        (
            "material",
            "Песок",
            "ГОСТ 8736-85",
            "ГОСТ 8736-85 Песок для строительных работ",
        ),
        (
            "standard_product",
            "Труба ПЭ100 SDR11 110*10",
            "ГОСТ Р 70628.1-2023",
            "ГОСТ Р 70628.1-2023 Трубы из полиэтилена",
        ),
        (
            "designed_structure",
            "Резервуар противопожарный V=100м3",
            "",
            "Резервуары противопожарные. Объем: 100 м3",
        ),
    ],
)
async def test_reference_does_not_invent_a_product_or_manufacturer(
    item_type, model, reference, text
):
    """Стандарт и проектный справочник остаются справочными доказательствами."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"pages": [{"page": 1, "text": text}]}
            )
        )
    ) as client:
        result = await DocumentServiceInspector(client, "http://document").inspect(
            EquipmentIdentity("", model, item_type=item_type, reference=reference),
            DownloadedDocument(
                b"%PDF-test", "https://library.example/book", "application/pdf"
            ),
        )
    assert result.applicable and result.applicability == "technical_reference"
    assert result.manufacturer == ""


async def test_search_partial_results_keep_engine_diagnostics():
    """HTTP 200 с полезной ссылкой используется независимо от отказа части движков."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [{"url": "https://maker.example/passport"}],
                    "unresponsive_engines": [["brave", "too many requests"]],
                },
            )
        )
    ) as client:
        hits = await SearxngSearch(client, "http://searx").search(
            "ВСКМ 90-20 паспорт", 8
        )
    assert len(hits) == 1 and hits.engines[0]["engine"] == "brave"


@pytest.mark.parametrize("status", [429, 503])
async def test_retry_uses_backoff_and_stops(monkeypatch, status):
    """Не выполняет немедленные или бесконечные повторы ограниченной выдачи."""
    delay = AsyncMock()
    monkeypatch.setattr(
        "pdrd_equipment_search_service.infrastructure.searxng.asyncio.sleep", delay
    )
    calls = []

    def response(request):
        """Считает попытки до устойчивого отказа."""
        calls.append(request)
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(SearchUnavailable):
            await SearxngSearch(client, "http://searx").search("паспорт", 8)
    assert len(calls) == 2 and delay.await_count == 1
    assert delay.call_args.args[0] >= 0.5


def test_discovery_bounds_depth_independent_urls_and_redacts_tokens():
    """Секреты не попадают в диагностику, активные ссылки не становятся кандидатами."""
    content = (
        b'<a href="/doc?token=secret&id=1">Passport</a>'
        + b'<a href="/other">Manual</a>'
    ) * 1000
    assert len(discover_links(content, "https://maker.example/", 1)) == 1
    assert (
        public_url("https://maker.example/doc?token=secret&id=1#part")
        == "https://maker.example/doc?id=1"
    )
    assert public_url("https://user:password@maker.example/doc") == ""


def test_long_navigation_does_not_hide_manufacturer_documents():
    """Паспорта после меню из 300 ссылок остаются кандидатами; HTTPS проверяется отдельно."""
    content = (
        b'<a href="/menu">Menu</a>' * 300
        + '<table><tr><td>ВСХНд-50 руководство</td><td><a href="http://maker.example/files/manual.PDF">PDF</a></td></tr></table>'.encode()
    )
    links = discover_links(
        content, "https://maker.example/", identity=EquipmentIdentity("", "ВСХНд-50")
    )
    assert links[0].url == "https://maker.example/files/manual.PDF"
    assert "ВСХНд-50" in links[0].title


def test_pdf_icons_use_whole_row_and_rank_supported_document_before_archive():
    """Подпись после значка тоже относится к PDF; меню и соцсети не расходуют квоту."""
    content = """<div>Документация <a href="https://social.example/">Мы в соцсетях</a></div>
    <table>
    <tr><td><a href="/DN15-40.pdf"><img src="icon.png"></a></td><td>Руководство ВСХНд DN 15-40</td></tr>
    <tr><td><a href="/DN40-250.pdf"><img src="icon.png"></a></td><td>Руководство ВСХНд DN 40-250</td></tr>
    <tr><td><a href="/DN40-250.rar">Скачать</a></td><td>ВСХНд документы</td></tr>
    </table>""".encode()
    links = discover_links(
        content, "https://maker.example/", 3, identity=EquipmentIdentity("", "ВСХНд-50")
    )
    assert links[0].url == "https://maker.example/DN40-250.pdf"
    assert "DN 40-250" in links[0].title
    assert all("social.example" not in hit.url for hit in links)
    assert links[-1].url.endswith(".rar")


def test_javascript_access_challenge_is_reported():
    """HTML 200 с challenge не является прочитанным техническим документом."""
    from pdrd_equipment_search_service.domain.discovery import html_access_issue

    assert (
        html_access_issue(b"<html>Please make sure you have enabled JavaScript</html>")
        == "javascript_required"
    )


async def test_checkpointed_download_is_reused_without_network_or_remaining_download_quota():
    """Рестарт после загрузки продолжает оценку bytes даже при недоступном поисковике."""
    from hashlib import sha256
    from types import SimpleNamespace

    from pdrd_equipment_search_service.application.resolve import ResolveEquipment
    from pdrd_equipment_search_service.application.search_budget import (
        EquipmentSearchBudget,
    )
    from pdrd_equipment_search_service.domain.discovery import candidate_id
    from pdrd_equipment_search_service.domain.equipment import (
        DocumentInspection,
        DocumentSnapshot,
        SourceDomain,
        SourceStatus,
    )

    url = "https://maker.example/passport.pdf"
    doc = DownloadedDocument(b"%PDF-checkpoint", url, "application/pdf")
    identity = EquipmentIdentity("Maker", "M-20")
    snapshot = DocumentSnapshot(
        "EQ-cached",
        "Maker",
        "M-20",
        "",
        url,
        url,
        doc.sha256,
        "snapshot",
        "",
        "trusted",
    )
    catalog = SimpleNamespace(
        find_document=AsyncMock(return_value=None),
        trusted_hosts=AsyncMock(return_value=()),
        source_for=AsyncMock(
            return_value=SourceDomain("Maker", "maker.example", SourceStatus.TRUSTED)
        ),
        load_candidate_download=AsyncMock(return_value=doc),
        save_document=AsyncMock(return_value=snapshot),
    )
    search = SimpleNamespace(
        search=AsyncMock(side_effect=RuntimeError("Поиск недоступен"))
    )
    download = SimpleNamespace(
        download=AsyncMock(side_effect=RuntimeError("Новая загрузка запрещена"))
    )
    inspector = SimpleNamespace(
        inspect=AsyncMock(return_value=DocumentInspection(True, manufacturer="Maker"))
    )
    candidate = {
        "candidate_id": candidate_id(url),
        "document_url": url,
        "download_status": "downloaded",
        "sha256": doc.sha256,
        "media_type": doc.media_type,
        "retryable": True,
        "attempt_count": 1,
    }
    previous = {
        "candidates": [candidate],
        "tried_url_hashes": [sha256(url.encode()).hexdigest()],
    }
    budget = EquipmentSearchBudget(0, 0)
    result = await ResolveEquipment(catalog, search, download, inspector).execute(
        identity, round_index=0, previous=previous, search_budget=budget
    )
    assert result.status == "found" and result.document.source_id == "EQ-cached"
    assert budget.downloads_remaining == 0
    search.search.assert_not_awaited()
    download.download.assert_not_awaited()
