# tests/functional/test_shared_equipment_search.py

"""SearXNG → PDF/HTML → реальные HTTP Document/Knowledge → программное EQ-сравнение."""

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import httpx
import pytest
from fastapi import FastAPI
from pdrd_analysis_service.application.engineering_program import (
    verified_equipment_check,
)
from pdrd_analysis_service.application.equipment_pipeline import compare_equipment_stage
from pdrd_analysis_service.transport.http.schemas import FindingDraftPayload
from pdrd_document_service.application.use_cases.equipment_document import (
    ExtractEquipmentDocument,
)
from pdrd_document_service.infrastructure.equipment_document import (
    PyMuPdfEquipmentDocumentReader,
)
from pdrd_document_service.transport.http.dependencies import (
    get_container as document_container,
)
from pdrd_document_service.transport.http.routers.equipment_document import (
    router as document_router,
)
from pdrd_equipment_search_service.application.resolve import ResolveEquipment
from pdrd_equipment_search_service.domain.equipment import (
    DocumentSnapshot,
    DownloadedDocument,
    EquipmentIdentity,
    SourceDomain,
    SourceStatus,
)
from pdrd_equipment_search_service.infrastructure.inspect_document import (
    DocumentServiceInspector,
)
from pdrd_equipment_search_service.infrastructure.knowledge_facts import (
    KnowledgeEquipmentFactIndex,
)
from pdrd_equipment_search_service.infrastructure.searxng import SearxngSearch
from pdrd_knowledge_service.application.equipment_fact_index import EquipmentFactIndex
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container as knowledge_container,
)
from pdrd_knowledge_service.transport.http.routers.equipment_facts import (
    router as knowledge_router,
)


class FactsCache:
    """Подменяет только PostgreSQL, сохраняя семантику immutable-набора фактов."""

    def __init__(self):
        """Создаёт пустое хранилище."""
        self.values = {}

    async def get(self, source_id):
        """Возвращает точный сохранённый набор."""
        return self.values.get(source_id)

    async def save(self, result):
        """Повтор не изменяет прежний набор."""
        self.values.setdefault(result["document_source_id"], result)


def passport(media_type):
    """Создаёт настоящие bytes документа с разными колонками моделей."""
    text = "MEAN WELL DRC-100B\nParameter | DRC-100A | DRC-100B\nOutput voltage | 12...15 V DC | 21...29 V DC"
    if media_type == "text/html":
        # Свойство после прежнего лимита 20k не теряется при HTTP-передаче.
        return (
            "<html><body>"
            + "<div>Public introduction</div>" * 1200
            + "<h1>MEAN WELL DRC-100B</h1><table><tr><th>Parameter</th><th>DRC-100A</th><th>DRC-100B</th></tr>"
            + "<tr><td>Output voltage</td><td>12...15 V DC</td><td>21...29 V DC</td></tr></table>"
            + "<script>Wrong instruction 500 V</script></body></html>"
        ).encode()
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), text, fontsize=10)
        return pdf.tobytes()


def project(value="30"):
    """Входные проектные факты, а не заранее сформулированное замечание."""
    return [
        {
            "page_number": 2,
            "facts": {
                "equipment_identities": [
                    {
                        "manufacturer": "MEAN WELL",
                        "model": "DRC-100B",
                        "confidence": 0.99,
                        "status": "resolved",
                        "object_ref": "PS1",
                        "evidence_text": "MEAN WELL DRC-100B",
                        "parameters": [
                            {
                                "property_name": "output_voltage",
                                "value_raw": value,
                                "unit_raw": "V",
                                "current_type": "DC",
                                "role": "output",
                                "evidence_text": f"PS1 Output voltage {value} V DC",
                            }
                        ],
                    }
                ]
            },
        }
    ]


@pytest.mark.parametrize("media_type", ["application/pdf", "text/html"])
@pytest.mark.parametrize("discovery", [False, True])
@pytest.mark.parametrize(
    ("source_status", "override", "allowed"),
    [
        (SourceStatus.TRUSTED, False, True),
        (SourceStatus.PENDING, False, False),
        (SourceStatus.PENDING, True, True),
        (SourceStatus.BLOCKED, True, False),
    ],
)
async def test_shared_search_document_facts_and_comparison(
    media_type, source_status, override, allowed, discovery
):
    """Разрешение реально включает извлечение/проверку; blocked не обходится галкой."""
    document_app, knowledge_app = FastAPI(), FastAPI()
    document_app.include_router(document_router)
    knowledge_app.include_router(knowledge_router)
    document_app.dependency_overrides[document_container] = lambda: SimpleNamespace(
        extract_equipment_document=ExtractEquipmentDocument(
            PyMuPdfEquipmentDocumentReader()
        )
    )
    fact_index = EquipmentFactIndex(FactsCache())
    knowledge_app.dependency_overrides[knowledge_container] = lambda: SimpleNamespace(
        equipment_fact_index=fact_index
    )
    transports = {
        "document-service": httpx.ASGITransport(document_app),
        "knowledge-service": httpx.ASGITransport(knowledge_app),
    }
    content = passport(media_type)
    requests = []

    async def dispatch(request):
        """Подменяет только поисковый Интернет; DTO и extraction настоящие."""
        requests.append((request.url.host, request.url.path))
        if request.url.host == "searxng":
            assert request.url.params["format"] == "json"
            assert "PS1" not in request.url.params["q"]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://maker.example/passport",
                            "title": "DRC-100B passport",
                        }
                    ]
                },
            )
        return await transports[request.url.host].handle_async_request(request)

    class Downloads:
        """Изолирует внешнюю загрузку; SSRF/TLS проверяются отдельными тестами адаптера."""

        def __init__(self):
            """Считает настоящие попытки получить файл."""
            self.calls = 0

        async def download(self, url, **kwargs):
            """Возвращает реальные PDF/HTML bytes."""
            self.calls += 1
            if discovery and url.endswith("/passport"):
                return DownloadedDocument(
                    b'<html><a href="/download?id=9">Passport PDF</a></html>',
                    url,
                    "text/html",
                )
            return DownloadedDocument(content, url, media_type)

    source = SourceDomain("MEAN WELL", "maker.example", source_status)
    catalog = SimpleNamespace(
        find_document=AsyncMock(return_value=None),
        source_for=AsyncMock(return_value=source),
        trusted_hosts=AsyncMock(
            return_value=("maker.example",)
            if source_status is SourceStatus.TRUSTED
            else ()
        ),
        manufacturer_aliases=AsyncMock(return_value=("MEAN WELL",)),
        manufacturer_profiles=AsyncMock(return_value=()),
        register_pending=AsyncMock(),
        document_pages=AsyncMock(),
        document_vision_facts=AsyncMock(),
    )

    async def save_document(identity, url, downloaded, inspection, trust_status):
        """Сохраняет только тестовый порт; production-сервисы и их БД здесь не запускаются."""
        saved = DocumentSnapshot(
            "EQ-" + downloaded.sha256[:32],
            identity.manufacturer,
            identity.model,
            identity.variant,
            url,
            downloaded.final_url,
            downloaded.sha256,
            "snapshot",
            inspection.revision,
            trust_status,
        )
        catalog.find_document.return_value = saved
        catalog.document_pages.return_value = inspection.pages
        catalog.document_vision_facts.return_value = inspection.vision_facts
        return saved

    catalog.save_document = save_document
    downloader = Downloads()
    async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as client:
        resolver = ResolveEquipment(
            catalog,
            SearxngSearch(client, "http://searxng:8080"),
            downloader,
            DocumentServiceInspector(
                client, "http://document-service", catalog=catalog
            ),
            fact_index=KnowledgeEquipmentFactIndex(client, "http://knowledge-service"),
        )
        identity = EquipmentIdentity(
            "MEAN WELL", "DRC-100B", properties=("output_voltage",)
        )
        resolved = await resolver.execute(identity, allow_unverified=override)
        if not allowed:
            assert resolved.document is None and downloader.calls == 0
            assert not any(host in transports for host, _ in requests)
            return
        assert resolved.status == "found" and resolved.facts
        assert all(
            f["document_source_id"] == resolved.document.source_id
            for f in resolved.facts
        )
        result = compare_equipment_stage(
            project(),
            {
                "status": "completed",
                "allow_unverified": override,
                "results": [asdict(resolved)],
            },
        )
        assert result["comparisons"][0]["status"] == "mismatch_candidate"
        finding = FindingDraftPayload.model_validate(
            result["candidates_by_page"]["2"][0]
        ).to_domain()
        assert finding.status == (
            "confirmed" if source_status is SourceStatus.TRUSTED else "needs_review"
        )
        assert verified_equipment_check(finding) is (
            source_status is SourceStatus.TRUSTED
        )
        assert (
            finding.equipment_documentation_basis_sources[0].sha256
            == resolved.document.sha256
        )
        assert finding.equipment_documentation_basis_sources[0].page == 1
        matched = compare_equipment_stage(
            project("24"),
            {
                "status": "completed",
                "allow_unverified": override,
                "results": [asdict(resolved)],
            },
        )
        assert matched["comparisons"][0]["status"] == "matched"
        assert matched["candidates_by_page"] == {}
        searches_before = len([r for r in requests if r[0] == "searxng"])
        warm = await resolver.execute(identity, allow_unverified=override)
        assert warm.status == "local" and warm.facts == resolved.facts
        assert downloader.calls == (2 if discovery else 1)
        if discovery:
            assert resolved.document.source_url == "https://maker.example/download?id=9"
            assert any(
                row["discovery_method"] == "html_link" and row.get("source_id")
                for row in resolved.candidates
            )
        assert len([r for r in requests if r[0] == "searxng"]) == searches_before
