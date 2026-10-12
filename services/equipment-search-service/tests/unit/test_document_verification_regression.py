# services/equipment-search-service/tests/unit/test_document_verification_regression.py

"""Алиасы, типографика и строгое подтверждение исполнения и производителя."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pdrd_equipment_search_service.domain.equipment import (
    DownloadedDocument,
    EquipmentIdentity,
)
from pdrd_equipment_search_service.infrastructure.inspect_document import (
    DocumentServiceInspector,
)


async def inspect(identity, pages, profiles=None):
    """Извлекает заданные страницы через настоящий HTTP-контракт без VLM."""
    catalog = SimpleNamespace(
        manufacturer_aliases=AsyncMock(return_value=("Болид", "bolid")),
        manufacturer_profiles=AsyncMock(
            return_value=profiles or ({"name": "Болид", "aliases": ["bolid"]},)
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "pages": [
                        {"page": i + 1, "text": text} for i, text in enumerate(pages)
                    ]
                },
            )
        )
    ) as client:
        return await DocumentServiceInspector(
            client, "http://document", catalog=catalog
        ).inspect(
            identity,
            DownloadedDocument(
                b"%PDF-test", "https://bolid.ru/manual.pdf", "application/pdf"
            ),
        )


@pytest.mark.parametrize(
    "text",
    [
        "BOLID РИП — 12 исполнение 51",
        "Болид Р И П - 1 2 исп. 51",
        "Болид РИП-12 исп 51",
    ],
)
async def test_alias_spacing_and_execution_are_confirmed(text):
    """Подтверждённый alias и незначащие пробелы не создают ложное несовпадение."""
    result = await inspect(EquipmentIdentity("Болид", "РИП-12", "исп. 51"), [text])
    assert result.applicable and result.manufacturer == "Болид"


@pytest.mark.parametrize(
    "text", ["Болид РИП-12 исп. 510", "Болид РИП-12 исп. 52", "Болид РИП-12Б исп. 51"]
)
async def test_neighboring_model_or_execution_is_rejected(text):
    """Соседняя модель и исполнение не становятся доказательством применимости."""
    result = await inspect(EquipmentIdentity("Болид", "РИП-12", "исп. 51"), [text])
    assert not result.applicable
    assert result.reason_code in {"model_mismatch", "variant_mismatch"}


async def test_execution_on_another_model_page_is_rejected():
    """Исполнение соседней модели на другом листе не подтверждает требуемую."""
    result = await inspect(
        EquipmentIdentity("Болид", "РИП-12", "исп. 51"),
        ["Болид РИП-12 исп. 52", "Болид РИП-24 исп. 51"],
    )
    assert not result.applicable and result.reason_code == "variant_mismatch"


@pytest.mark.parametrize("separator", ["\n", "; "])
async def test_execution_of_other_model_on_same_page_is_rejected(separator):
    """Одного листа недостаточно: исполнение должно относиться именно к нужной модели."""
    result = await inspect(
        EquipmentIdentity("Болид", "РИП-12", "исп. 51"),
        ["Болид РИП-12 исп. 52" + separator + "Болид РИП-24 исп. 51"],
    )
    assert not result.applicable and result.reason_code == "variant_mismatch"


async def test_article_and_execution_must_belong_to_the_same_model_occurrence():
    """Артикул второго исполнения не подтверждает первое при повторе базовой модели."""
    text = "Болид РИП-12 исп. 51 SKU11\nБолид РИП-12 исп. 52 SKU22"
    wrong = await inspect(
        EquipmentIdentity("Болид", "РИП-12", "исп. 51", article="SKU22"), [text]
    )
    correct = await inspect(
        EquipmentIdentity("Болид", "РИП-12", "исп. 51", article="SKU11"), [text]
    )
    assert not wrong.applicable and wrong.reason_code == "article_mismatch"
    assert correct.applicable


async def test_complex_applicability_targets_the_model_page():
    """Неоднозначная таблица направляет VLM на нужный лист, не на начало паспорта."""
    pages = ["Предисловие"] * 6 + ["Болид. Таблица: РИП-12 исп. 52\nРИП-24 исп. 51"]
    result = await inspect(EquipmentIdentity("Болид", "РИП-12", "исп. 51"), pages)
    assert not result.applicable and result.requires_vision
    assert result.vision_pages == (7,)


async def test_unknown_manufacturer_is_confirmed_only_by_registered_document_alias():
    """Предварительный поиск не назначает бренд без документального доказательства."""
    identity = EquipmentIdentity(
        "",
        "Сигнал-10",
        "2RS485",
        status="needs_review",
        manufacturer_status="unknown",
        variant_kind="interface",
    )
    result = await inspect(identity, ["BOLID Сигнал-10. Руководство"])
    assert result.applicable and result.manufacturer == "Болид"
    ambiguous = await inspect(
        identity,
        ["BOLID Рубеж Сигнал-10"],
        profiles=(
            {"name": "Болид", "aliases": ["bolid"]},
            {"name": "Рубеж", "aliases": []},
        ),
    )
    assert not ambiguous.applicable and ambiguous.reason_code == "manufacturer_unknown"
    separate = await inspect(
        EquipmentIdentity("Болид", "Сигнал-10"),
        ["Другой изготовитель Сигнал-10", "BOLID каталог"],
    )
    assert not separate.applicable and separate.reason_code == "manufacturer_mismatch"
