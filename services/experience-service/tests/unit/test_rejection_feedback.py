# services/experience-service/tests/unit/test_rejection_feedback.py

"""Объяснение отказа проходит через домен, историю, каталог и обучающую проекцию."""

from dataclasses import replace

import pytest
from pdrd_experience_service.application.catalog_snapshot import (
    example_from_json,
    example_to_json,
)
from pdrd_experience_service.domain.artifact_projection import (
    artifact_member_projection,
)
from pdrd_experience_service.domain.catalog import CatalogEntry
from pdrd_experience_service.domain.catalog_identity import (
    content_key,
    snapshot_content_key,
)
from pdrd_experience_service.domain.experience_selection import (
    select_experience_candidates,
)
from pdrd_experience_service.domain.index_projection import index_projection
from pdrd_experience_service.domain.rejection_feedback import REASON_CATEGORIES
from pdrd_experience_service.domain.review import (
    Decision,
    ProposedRegion,
    Rectangle,
    ReviewError,
)
from pdrd_experience_service.infrastructure.database.codec import (
    finding_from_json,
    finding_to_json,
)

from .test_catalog import example
from .test_review_domain import NOW, opened


def reject(session, reason="false_positive", comment="", expected=None):
    """Меняет одно серверное замечание с явной причиной и ожидаемой редакцией."""
    return session.decide(
        finding_id="vlm:1",
        decision=Decision.REJECTED,
        reason_category=reason,
        comment=comment,
        actor="engineer:1",
        at=NOW,
        expected_revision=session.revision if expected is None else expected,
    )


@pytest.mark.parametrize("reason", sorted(REASON_CATEGORIES))
def test_each_reason_and_optional_comment_roundtrip(reason):
    """Стабильные категории не заменяются русскими подписями UI в JSONB."""
    session = reject(opened(), reason, "  Это резервный насос, значение корректно  ")
    row = session.findings[0]
    assert row.reason_category == reason
    assert row.comment == "Это резервный насос, значение корректно"
    assert finding_from_json(finding_to_json(row)) == row
    assert session.history[-1].after == row
    assert session.history[-1].before.reason_category is None


@pytest.mark.parametrize(
    "reason,comment",
    [(None, ""), ("unknown", ""), (True, ""), ("other", None), ("other", "я" * 2001)],
)
def test_invalid_feedback_never_changes_review(reason, comment):
    """Новый отказ без категории или с неверным комментарием не создаёт историю."""
    session = opened()
    with pytest.raises((ReviewError, ValueError)):
        reject(session, reason, comment)
    assert session.revision == 0
    assert session.findings[0].decision is Decision.PENDING


def test_reason_change_is_audited_and_identical_repeat_is_noop():
    """Повтор не дублирует аудит; изменение объяснения создаёт новую редакцию."""
    first = reject(opened(), "wrong_location", "Не тот насос")
    assert reject(first, "wrong_location", "Не тот насос") is first
    second = reject(first, "misunderstood_drawing", "Это резервный насос")
    assert second.revision == first.revision + 1
    assert second.history[-1].before.comment == "Не тот насос"
    assert second.history[-1].after.comment == "Это резервный насос"
    assert second.findings[0].experience_tag == "bad"


@pytest.mark.parametrize("action", ["accept", "edit", "reset", "geometry"])
def test_new_decision_or_revision_clears_only_current_feedback(action):
    """Объяснение старого отказа остаётся в истории после повторной проверки."""
    original = opened()
    if action == "geometry":
        original = replace(
            original,
            findings=(
                replace(
                    original.findings[0],
                    proposed_regions=(
                        ProposedRegion(Rectangle(5, 10, 50, 100), "vlm", 0.9, "vlm"),
                    ),
                ),
                original.findings[1],
            ),
        )
    refused = reject(original, "duplicate", "Повтор замечания 2")
    args = {
        "finding_id": "vlm:1",
        "actor": "engineer:1",
        "at": NOW,
        "expected_revision": 1,
    }
    if action == "accept":
        changed = refused.decide(decision=Decision.ACCEPTED, **args)
    elif action == "edit":
        changed = refused.edit(
            text="Новая формулировка", normative_basis="СП 2", **args
        )
    elif action == "reset":
        changed = refused.reset_decision(**args)
    else:
        changed = refused.change_geometry(
            regions=(Rectangle(10, 20, 100, 200),), callout_box=None, **args
        )
    assert changed.findings[0].reason_category is None
    assert changed.findings[0].comment == ""
    assert changed.history[-1].before.comment == "Повтор замечания 2"
    assert changed.history[-2].after.reason_category == "duplicate"


def test_accepted_command_cannot_carry_rejection_feedback():
    """Домен повторно проверяет правило, даже при вызове без HTTP-схемы."""
    with pytest.raises(ReviewError):
        opened().decide(
            finding_id="vlm:1",
            decision=Decision.ACCEPTED,
            reason_category="other",
            actor="engineer:1",
            at=NOW,
            expected_revision=0,
        )


def test_legacy_review_and_catalog_keep_absent_reason():
    """Совместимость не приписывает старым отказам неизвестное объяснение."""
    row = finding_to_json(reject(opened()).findings[0])
    del row["reason_category"], row["comment"]
    restored = finding_from_json(row)
    assert restored.decision is Decision.REJECTED
    assert restored.reason_category is None and restored.comment == ""
    data = example_to_json(example(tag="bad", decision=Decision.REJECTED))
    del data["source"]["reason_category"], data["source"]["comment"]
    restored_example = example_from_json(data)
    assert restored_example.source.reason_category is None
    assert restored_example.source.comment == ""
    assert content_key(restored_example.source) == snapshot_content_key(data["source"])


def test_catalog_selection_preserves_feedback_for_unlocated_rejection():
    """Текстовый отказ сохраняется для обучения без придуманной области."""
    review = reject(
        opened(), "not_applicable", "Требование относится к рабочему насосу"
    )
    review = review.decide(
        finding_id="vlm:2",
        decision=Decision.ACCEPTED,
        actor="engineer:1",
        at=NOW,
        expected_revision=review.revision,
    )
    review = review.approve(
        actor="engineer:1", at=NOW, expected_revision=review.revision
    )
    candidates = select_experience_candidates(
        session=review, confirmed_areas=(), for_catalog=True
    )
    source = candidates[0]
    assert source.reason_category == "not_applicable"
    assert source.comment == "Требование относится к рабочему насосу"
    assert source.issue_regions == ()


def test_feedback_changes_deduplication_and_index_fingerprint():
    """Другие объяснения не теряются при повторном сохранении одинакового замечания."""
    legacy = example(tag="bad", decision=Decision.REJECTED)
    first = replace(
        legacy,
        source=replace(
            legacy.source, reason_category="false_positive", comment="Резервный насос"
        ),
    )
    second = replace(
        first, source=replace(first.source, reason_category="wrong_location")
    )
    third = replace(first, source=replace(first.source, comment="Корректное значение"))
    assert len({content_key(row.source) for row in (legacy, first, second, third)}) == 4
    payload = index_projection(CatalogEntry(first, True))
    assert payload["reason_category"] == "false_positive"
    assert payload["comment"] == "Резервный насос"
    for kind in ("vector", "fine_tune"):
        member = artifact_member_projection(CatalogEntry(first, True), kind=kind)
        assert member["reason_category"] == "false_positive"
        assert member["comment"] == "Резервный насос"
    assert (
        payload["fingerprint"]
        != index_projection(CatalogEntry(second, True))["fingerprint"]
    )
    assert "reason_category" not in index_projection(CatalogEntry(legacy, True))
    assert example_from_json(example_to_json(first)) == first


def test_reason_does_not_resolve_ambiguous_edited_negative_target():
    """Категория отказа не выбирает сама объект отрицательного обучающего примера."""
    record = example(tag="edited", decision=Decision.REJECTED)
    record = replace(
        record,
        source=replace(
            record.source, reason_category="false_positive", comment="Ошибка"
        ),
    )
    assert record.learning_use == "needs_adjudication"
    assert index_projection(CatalogEntry(record, True)) is None
