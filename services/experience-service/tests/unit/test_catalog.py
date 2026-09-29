# services/experience-service/tests/unit/test_catalog.py

"""Правки опыта сохраняют источник, Gold и неоднозначную отрицательную разметку."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pdrd_experience_service.application.catalog_snapshot import (
    example_from_json,
    example_to_json,
)
from pdrd_experience_service.domain.catalog import CatalogEntry, Crop, Example
from pdrd_experience_service.domain.experience_selection import (
    ExperienceCandidate,
    LearningUse,
)
from pdrd_experience_service.domain.review import (
    Decision,
    Origin,
    Rectangle,
    ReviewError,
)

AT = datetime(2026, 9, 28, tzinfo=UTC)


def example(*, tag="wise", origin=Origin.VLM, decision=Decision.ACCEPTED):
    """Полный источник проверенного примера, не данные браузера."""
    candidate = ExperienceCandidate(
        "key",
        uuid4(),
        uuid4(),
        "План.pdf",
        "a" * 64,
        5,
        "vlm:1",
        2,
        origin,
        tag,
        decision,
        LearningUse.POSITIVE,
        1,
        (Rectangle(10, 20, 100, 200),),
        None,
        "Первоначальный текст",
        "Текущий текст",
        "Исходный норматив",
        "СП 1",
        "engineer:1",
        "engineer:1",
        "engineer:1",
        AT,
    )
    return Example(
        uuid4(),
        candidate,
        (Crop("b" * 64, 90, 180),),
        0,
        "План",
        candidate.text,
        candidate.normative_basis,
        "",
        True,
        "",
        "",
        AT,
        AT,
        "engineer:1",
    )


def test_originals_roundtrip_and_curation_never_change_source():
    """JSONB/экспорт не теряют оригинал, подтверждение, координаты и UTC."""
    original = example()
    assert example_from_json(example_to_json(original)) == original
    revised = original.curate(
        fields={"text": "Исправлено в каталоге", "active": False},
        actor="engineer:2",
        at=AT,
    )
    assert revised.source == original.source
    assert revised.crops == original.crops
    assert revised.tag == "edited"
    assert revised.learning_use == "positive"
    assert revised.revision == 1
    assert not CatalogEntry(revised, True).active
    assert not CatalogEntry(original, False).active


def test_gold_edit_never_becomes_vlm_and_bad_edit_needs_adjudication():
    """Происхождение важнее факта правки, а rejected Edited неоднозначен."""
    gold = example(tag="gold", origin=Origin.MANUAL)
    assert (
        gold.curate(fields={"text": "Новый Gold"}, actor="engineer:2", at=AT).tag
        == "gold"
    )
    bad = example(tag="bad", decision=Decision.REJECTED)
    assert bad.learning_use == "negative"
    revised = bad.curate(
        fields={"text": "Новая формулировка"}, actor="engineer:2", at=AT
    )
    assert revised.tag == "edited"
    assert revised.learning_use == "needs_adjudication"


@pytest.mark.parametrize("target", ["original", "revised", "both"])
def test_explicit_adjudication_requires_reason_and_preserves_both_texts(target):
    """Уточнение допускает negative, не меняя решения и прежних формулировок."""
    record = example(tag="edited", decision=Decision.REJECTED)
    assert record.learning_use == "needs_adjudication"
    revised = record.curate(
        fields={"rejection_reason": "Ошибка выдумана", "negative_target": target},
        actor="engineer:2",
        at=AT,
    )
    assert revised.learning_use == "negative"
    assert revised.source.original_text == record.source.original_text
    assert revised.source.text == record.source.text
    assert revised.source.decision is Decision.REJECTED


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"source": {}},
        {"tag": "gold"},
        {"text": " "},
        {"active": "true"},
        {"rejection_reason": "Нет ошибки"},
        {"negative_target": "original"},
        {"negative_target": "unknown", "rejection_reason": "Ошибка"},
        {"text": "a" * 10001},
    ],
)
def test_invalid_or_forged_fields_are_rejected(fields):
    """Пустые правки, подмена происхождения и неполная разметка не сохраняются."""
    with pytest.raises(ReviewError):
        example(tag="edited", decision=Decision.REJECTED).curate(
            fields=fields, actor="engineer:2", at=AT
        )


def test_accepted_example_cannot_receive_negative_reason():
    """Каталог не разрешает отрицательный target принятому положительному примеру."""
    with pytest.raises(ReviewError):
        example().curate(
            fields={"negative_target": "original", "rejection_reason": "Ошибка"},
            actor="engineer:2",
            at=AT,
        )


def test_editing_adjudicated_revision_requires_new_assessment():
    """Отрицательная оценка прежней правки не переносится на новый текст сама."""
    original = example(tag="edited", decision=Decision.REJECTED)
    adjudicated = original.curate(
        fields={"rejection_reason": "Неверное толкование", "negative_target": "both"},
        actor="engineer:2",
        at=AT,
    )
    assert adjudicated.learning_use == "negative"
    changed = adjudicated.curate(
        fields={"text": "Ещё одна формулировка"}, actor="engineer:2", at=AT
    )
    assert changed.learning_use == "needs_adjudication"
    assert not changed.rejection_reason and not changed.negative_target
