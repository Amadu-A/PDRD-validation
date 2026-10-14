# services/equipment-search-service/tests/unit/test_snapshot_read.py

"""Проверяет неизменность байтов сохранённого EQ-документа при выдаче."""

from hashlib import sha256
from types import SimpleNamespace

import pytest
from pdrd_equipment_search_service.infrastructure.postgres_catalog import (
    PostgresEquipmentCatalog,
)


class Result:
    """Имитирует строку PostgreSQL."""

    def __init__(self, row) -> None:
        """Сохраняет строку."""
        self.row = row

    def first(self):
        """Возвращает найденный документ."""
        return self.row


class Connection:
    """Имитирует чтение только одного source ID."""

    def __init__(self, row) -> None:
        """Сохраняет строку."""
        self.row = row

    async def __aenter__(self):
        """Открывает соединение."""
        return self

    async def __aexit__(self, *_):
        """Закрывает соединение."""

    async def execute(self, statement, params):
        """Проверяет запрос по точному идентификатору."""
        assert params["source_id"] == self.row.source_id
        return Result(self.row)


class Engine:
    """Возвращает тестовое соединение."""

    def __init__(self, row) -> None:
        """Сохраняет строку."""
        self.row = row

    def connect(self):
        """Открывает контекст чтения."""
        return Connection(self.row)


@pytest.mark.asyncio
async def test_snapshot_bytes_match_saved_sha(tmp_path) -> None:
    """Доступен только сохранённый файл с проверенной контрольной суммой."""
    content = b"%PDF-1.7 original"
    digest = sha256(content).hexdigest()
    relative = f"{digest[:2]}/{digest}.pdf"
    target = tmp_path / relative
    target.parent.mkdir()
    target.write_bytes(content)
    row = SimpleNamespace(
        source_id="EQ-" + "a" * 32,
        manufacturer="MEAN WELL",
        model="DRC-100B",
        variant="",
        source_url="https://www.meanwell.com/original.pdf",
        final_url="https://www.meanwell.com/original.pdf",
        sha256=digest,
        storage_reference=relative,
        revision="B",
        trust_status="trusted",
        media_type="application/pdf",
    )
    catalog = PostgresEquipmentCatalog(Engine(row), tmp_path)

    snapshot, loaded, media_type = await catalog.load_document(row.source_id)
    assert snapshot.source_id == row.source_id
    assert loaded == content
    assert media_type == "application/pdf"

    target.write_bytes(b"%PDF-1.7 modified")
    with pytest.raises(ValueError, match="Контрольная сумма"):
        await catalog.load_document(row.source_id)
