# tests/functional/test_equipment_unverified_report.py

"""Разрешённый pending PDF проходит реальное чтение, факты и сравнение без повышения доверия."""

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
from pdrd_analysis_service.application.equipment_pipeline import compare_equipment_stage
from pdrd_equipment_search_service.application.resolve import ResolveEquipment
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DocumentSnapshot,
    DownloadedDocument,
    EquipmentIdentity,
    SearchHit,
    SourceDomain,
    SourceStatus,
)
from pdrd_knowledge_service.application.equipment_facts import extract_equipment_facts


def dependencies(status=SourceStatus.PENDING, *, enabled=True, official=False):
    """Подготавливает реальный PDF и управляемые сетевые порты без внешнего поиска."""
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 60), "ACME\nMODEL-1 Output voltage 30 V DC")
        content = pdf.tobytes()
    downloaded = DownloadedDocument(
        content, "https://docs.example/model-1.pdf", "application/pdf"
    )
    source = SourceDomain(
        "ACME", "docs.example", status, enabled=enabled, registry_official=official
    )

    async def inspect(identity, document):
        """Читает реальные байты и проверяет точную модель и имя изготовителя."""
        with fitz.open(stream=document.content, filetype="pdf") as pdf:
            text = pdf[0].get_text()
        return DocumentInspection(
            identity.model in text and "ACME" in text,
            pages=((1, text),),
            manufacturer="ACME",
        )

    async def save(identity, url, document, inspection, trust_status):
        """Создаёт тот же неизменяемый snapshot после успешной проверки применимости."""
        return DocumentSnapshot(
            "EQ-" + document.sha256[:32],
            identity.manufacturer,
            identity.model,
            identity.variant,
            url,
            document.final_url,
            document.sha256,
            "snapshot.pdf",
            "",
            trust_status,
        )

    async def facts(identity, snapshot, pages, **kwargs):
        """Использует production extractor характеристик, а не заранее выбранное расхождение."""
        return "ready", extract_equipment_facts(
            source_id=snapshot.source_id,
            model=identity.model,
            variant=identity.variant,
            pages=[{"page": page, "text": text} for page, text in pages],
        )

    catalog = SimpleNamespace(
        find_document=AsyncMock(return_value=None),
        trusted_hosts=AsyncMock(return_value=()),
        source_for=AsyncMock(return_value=source),
        register_pending=AsyncMock(),
        save_document=AsyncMock(side_effect=save),
    )
    downloader = SimpleNamespace(download=AsyncMock(return_value=downloaded))
    resolver = ResolveEquipment(
        catalog,
        SimpleNamespace(
            search=AsyncMock(return_value=(SearchHit(downloaded.final_url),))
        ),
        downloader,
        SimpleNamespace(inspect=AsyncMock(side_effect=inspect)),
        fact_index=SimpleNamespace(extract_or_get=AsyncMock(side_effect=facts)),
    )
    return resolver, source, downloader, catalog


def project():
    """Проектный факт содержит подтверждённые 24 В для той же модели и выходного режима."""
    return [
        {
            "page_number": 1,
            "facts": {
                "equipment_identities": [
                    {
                        "manufacturer": "ACME",
                        "model": "MODEL-1",
                        "status": "resolved",
                        "confidence": 1,
                        "object_ref": "PS1",
                        "evidence_text": "PS1 MODEL-1 24 V DC",
                        "parameters": [
                            {
                                "property_name": "output_voltage",
                                "value_raw": "24",
                                "unit_raw": "V",
                                "role": "output",
                                "current_type": "DC",
                                "mode": "",
                                "evidence_text": "PS1 24 V DC",
                            }
                        ],
                    }
                ]
            },
        }
    ]


async def test_pending_source_requires_job_permission_then_creates_evidenced_mismatch():
    """Галочка разрешает чтение и сравнение, сохраняя pending и исходный URL snapshot."""
    resolver, source, downloader, catalog = dependencies()
    denied = await resolver.execute(
        EquipmentIdentity("ACME", "MODEL-1"), allow_unverified=False
    )
    assert denied.document is None
    downloader.download.assert_not_awaited()
    allowed = await resolver.execute(
        EquipmentIdentity("ACME", "MODEL-1"), allow_unverified=True
    )
    assert allowed.document.trust_status == "unverified_user_override"
    assert source.status is SourceStatus.PENDING and allowed.manufacturer_site is None
    result = compare_equipment_stage(
        project(), {"status": "completed", "results": [asdict(allowed)]}
    )
    finding = result["candidates_by_page"]["1"][0]
    assert finding["equipment_details"]["manufacturer_value"] == "30"
    assert (
        finding["equipment_documentation_basis_sources"][0]["trust_status"]
        == "unverified_user_override"
    )
    assert result["models"][0]["document"]["source_id"] == allowed.document.source_id
    assert (
        result["models"][0]["document"]["final_url"]
        == "https://docs.example/model-1.pdf"
    )
    assert catalog.save_document.await_count == 1


@pytest.mark.parametrize(
    "status, enabled", [(SourceStatus.BLOCKED, True), (SourceStatus.TRUSTED, False)]
)
async def test_blocked_or_disabled_sources_remain_forbidden_with_override(
    status, enabled
):
    """Разрешение на pending не отменяет блокировку или отключение сайта."""
    resolver, _, downloader, catalog = dependencies(
        status, enabled=enabled, official=True
    )
    result = await resolver.execute(
        EquipmentIdentity("ACME", "MODEL-1"), allow_unverified=True
    )
    assert result.document is None and result.manufacturer_site is None
    downloader.download.assert_not_awaited()
    catalog.save_document.assert_not_awaited()


@pytest.mark.parametrize("official", [False, True])
async def test_only_verified_official_registry_entry_exposes_manufacturer_site(
    official,
):
    """Доверенный дилерский URL не становится сайтом изготовителя."""
    resolver, _, _, _ = dependencies(SourceStatus.TRUSTED, official=official)
    result = await resolver.execute(EquipmentIdentity("ACME", "MODEL-1"))
    assert result.document is not None
    assert bool(result.manufacturer_site) is official
    if official:
        report = compare_equipment_stage(
            project(), {"status": "completed", "results": [asdict(result)]}
        )
        assert (
            report["models"][0]["manufacturer_site"]["url"] == "https://docs.example/"
        )


async def test_explicit_analog_metadata_cannot_create_project_violation():
    """Аналог остаётся справочной ссылкой, сохраняя документ и запрещая инженерное основание."""
    resolver, _, _, _ = dependencies(SourceStatus.TRUSTED, official=True)
    resolved = await resolver.execute(EquipmentIdentity("ACME", "MODEL-1"))
    result = asdict(resolved)
    result["manufacturer_site"]["relationship"] = "analog"
    compared = compare_equipment_stage(
        project(), {"status": "completed", "results": [result]}
    )
    assert compared["candidates_by_page"] == {} and compared["comparisons"] == []
    assert compared["models"][0]["status"] == "needs_identification"
