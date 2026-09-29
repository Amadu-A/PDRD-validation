# services/experience-service/tests/unit/test_catalog_identity.py

"""Одинаковый PDF между прогонами не размножает одинаковые инженерные примеры."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from pdrd_experience_service.application.catalog_snapshot import example_to_json
from pdrd_experience_service.domain.catalog_identity import (
    content_key,
    snapshot_content_key,
)
from pdrd_experience_service.domain.review import Decision, Rectangle

from .test_catalog import example


def test_job_ids_approval_time_and_card_position_do_not_change_identity():
    """Повторный прогон, другая группировка VLM и новое время не создают дубль."""
    original = example().source
    repeated = replace(
        original,
        job_id=uuid4(),
        document_id=uuid4(),
        finding_id="vlm:other",
        approved_revision=81,
        finding_revision=8,
        confirmed_at=original.confirmed_at + timedelta(days=1),
        confirmed_by="engineer:2",
        callout_box=Rectangle(1, 2, 3, 4),
    )
    assert content_key(original) == content_key(repeated)
    assert content_key(original) == snapshot_content_key(
        example_to_json(example())["source"]
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"source_sha256": "f" * 64},
        {"text": "Иная формулировка"},
        {"issue_regions": (Rectangle(11, 20, 100, 200),)},
        {"decision": Decision.REJECTED, "tag": "bad"},
        {"page_number": 2},
    ],
)
def test_new_semantic_example_is_not_deduplicated(fields):
    """Новая ошибка, решение или физическая область сохраняются отдельно."""
    original = example().source
    assert content_key(original) != content_key(replace(original, **fields))
