# services/api-gateway/tests/unit/test_equipment_update.py

"""EQ-дополнение берёт данные с сервера и не изменяет исходный результат."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.equipment_update import BuildEquipmentUpdate


async def test_update_uses_saved_inventory_and_selected_equipment_version():
    """Используются исходные параметры и страницы; браузер не подменяет проектные факты."""
    job_id, document_id = uuid4(), uuid4()
    original = {
        "equipment_check": {
            "enabled": True,
            "inventory": [
                {
                    "manufacturer": "",
                    "model": "РИП-12",
                    "variant": "исп. 51",
                    "status": "needs_review",
                    "manufacturer_status": "unknown",
                    "model_status": "confirmed",
                    "confidence": 0.95,
                    "occurrences": [
                        {
                            "page": 2,
                            "object_ref": "UG1",
                            "parameters": [{"value_raw": "12"}],
                        }
                    ],
                }
            ],
        },
        "findings": [{"finding_id": "N-existing"}],
    }
    before = deepcopy(original)
    results = SimpleNamespace(
        execute=AsyncMock(return_value=original),
        get_analysis_job=SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(document_id=document_id))
        ),
    )
    search = {"revision": 17, "status": "incomplete", "results": []}
    jobs = SimpleNamespace(version=AsyncMock(return_value=search), state=AsyncMock())
    comparison = SimpleNamespace(
        compare=AsyncMock(
            return_value={"candidates_by_page": {"2": [{"finding_id": "EQ-new"}]}}
        )
    )
    update = await BuildEquipmentUpdate(results, jobs, comparison).execute(
        job_id=job_id, revision=17
    )
    jobs.version.assert_awaited_once_with(document_id, 17)
    jobs.state.assert_not_awaited()
    pages, received = comparison.compare.await_args.args
    assert received is search
    assert pages[0]["page_number"] == 2
    assert pages[0]["facts"]["equipment_identities"][0]["parameters"] == [
        {"value_raw": "12"}
    ]
    assert update["findings"] == [{"finding_id": "EQ-new"}]
    assert original == before


async def test_disabled_equipment_does_not_call_search_or_comparison():
    """EQ opt-out не запускает HTTP, VLM или пересчёт дополнения."""
    results = SimpleNamespace(
        execute=AsyncMock(return_value={"equipment_check": {"enabled": False}})
    )
    jobs, comparison = (
        SimpleNamespace(state=AsyncMock()),
        SimpleNamespace(compare=AsyncMock()),
    )
    with pytest.raises(ReviewRequestError):
        await BuildEquipmentUpdate(results, jobs, comparison).execute(job_id=uuid4())
    jobs.state.assert_not_awaited()
    comparison.compare.assert_not_awaited()
