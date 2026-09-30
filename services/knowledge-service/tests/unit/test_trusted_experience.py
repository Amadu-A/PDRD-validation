# services/knowledge-service/tests/unit/test_trusted_experience.py

"""Индекс и поиск E: актуальность, мультимодальность, повтор, сбои и отрицательные примеры."""

from dataclasses import replace

import pytest
from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingProviderError,
)
from pdrd_knowledge_service.application.use_cases.index_experience import (
    SyncExperienceIndex,
)
from pdrd_knowledge_service.application.use_cases.trusted_experience import (
    SearchTrustedExperience,
)
from pdrd_knowledge_service.domain.project_context import VectorRecord
from pdrd_knowledge_service.domain.search import VectorPoint

from tests.experience_index_support import (
    PNG,
    MemoryFeed,
    MemoryVectors,
    RecordingEmbedding,
    trusted_example,
)


def services(examples=()):
    """Внедряет отдельные порты индекса/поиска, используя один физический набор точек."""
    source, embedding, vectors = (
        MemoryFeed(examples),
        RecordingEmbedding(),
        MemoryVectors(),
    )
    index = SyncExperienceIndex(
        source, embedding, vectors, "trusted", "identity", 2, page_size=1
    )
    search = SearchTrustedExperience(
        embedding, vectors, source, "trusted", "model", "identity", enabled=True
    )
    return source, embedding, vectors, index, search


async def test_multimodal_index_is_idempotent_and_keeps_targets_per_crop():
    """Edited negative/both: две формулировки на каждый crop, повтор не расходует GPU."""
    item = trusted_example(tag="edited", target="both", crops=2)
    feed, embedding, vectors, index, _ = services((item,))
    first = await index.execute()
    assert first["indexed"] == 4 and first["deleted"] == 0
    assert vectors.created == ("trusted", 2)
    assert len(embedding.calls) == 1
    assert {record.payload["target"] for record in vectors.records.values()} == {
        "original",
        "revised",
    }
    assert all(
        value.image_bytes == PNG and value.text and value.instruction
        for value in embedding.calls[0][0]
    )
    assert len(feed.crop_calls) == 2 and len(feed.verify_calls) == 1
    ids = set(vectors.records)
    repeated = await index.execute()
    assert repeated["unchanged"] == 4 and repeated["indexed"] == 0
    assert len(embedding.calls) == 1 and set(vectors.records) == ids


async def test_changed_catalog_revision_updates_stable_ids_and_drops_removed_targets():
    """После уточнения both → original старый revised удаляется после полного обхода."""
    before = trusted_example(tag="edited", target="both")
    feed, _, vectors, index, _ = services((before,))
    await index.execute()
    original_id = before.point_id(target="original", crop_index=0)
    after = trusted_example(tag="edited", target="original", revision=1)
    feed.examples, feed.current = (after,), {after.reference: after}
    result = await index.execute()
    assert result["indexed"] == 1 and result["deleted"] == 1
    assert set(vectors.records) == {original_id}
    assert vectors.records[original_id].payload["example_revision"] == 1


async def test_edited_during_embedding_is_not_written():
    """Внешняя embedding-модель не закрепляет уже отозванную редакцию."""
    feed, embedding, vectors, index, _ = services((trusted_example(),))
    embedding.after_embed = feed.current.clear
    result = await index.execute()
    assert result["stale"] == 1 and not vectors.records and not vectors.writes


@pytest.mark.parametrize("failure", ["page", "crop", "embedding"])
async def test_failed_sweep_does_not_purge_old_points(failure):
    """Неполный обход или ошибка вычисления не трактуется как пустой каталог."""
    feed, embedding, vectors, index, _ = services((trusted_example(),))
    foreign = VectorRecord("foreign", [1.0, 0.0], {"kind": "legacy"})
    old = VectorRecord(
        "old", [1.0, 0.0], {"kind": "human_review_v1", "index_point_id": "old"}
    )
    vectors.records = {"old": old, "foreign": foreign}
    if failure == "page":
        feed.fail_page_after = 1
    elif failure == "crop":
        feed.fail_crop = True
    else:
        embedding.answer = [[0.0, 0.0]]
    with pytest.raises((ExperienceFeedError, MultimodalEmbeddingProviderError)):
        await index.execute()
    assert (
        not vectors.deletes
        and "old" in vectors.records
        and "foreign" in vectors.records
    )


@pytest.mark.parametrize(
    "answer",
    [
        [],
        [[1.0]],
        [[True, 0.0]],
        [[float("nan"), 1.0]],
        [[float("inf"), 1.0]],
        [[0.0, 0.0]],
    ],
)
async def test_incompatible_embedding_never_reaches_qdrant(answer):
    """Число, размерность и конечность векторов проверяются до записи."""
    _, embedding, vectors, index, _ = services((trusted_example(),))
    embedding.answer = answer
    with pytest.raises(MultimodalEmbeddingProviderError):
        await index.execute()
    assert not vectors.writes and not vectors.deletes


async def test_successful_empty_sweep_purges_only_human_review_points():
    """Деактивированные примеры удаляются без воздействия на другой тип данных."""
    feed, _, vectors, index, _ = services((trusted_example(),))
    await index.execute()
    vectors.records["legacy"] = VectorRecord("legacy", [1.0, 0.0], {"kind": "legacy"})
    feed.examples, feed.current = (), {}
    result = await index.execute()
    assert result["deleted"] == 1 and set(vectors.records) == {"legacy"}


async def test_retrieval_uses_fresh_text_not_forged_payload_and_deduplicates_crops():
    """Qdrant хранит ссылки, а источник определяет текст/решение; crop не множат sources."""
    item = trusted_example(crops=2)
    feed, embedding, vectors, index, search = services((item,))
    await index.execute()
    for record in vectors.records.values():
        record.payload.update(text="Выдуманная ошибка", tag="bad", verified_fixed=True)
    result, repeated = await search.execute([" Маркировка ", "Маркировка"])
    assert result == repeated and len(result.sources) == 1
    source = result.sources[0]
    assert source.issue_text == item.data["text"] and source.tag == "wise"
    assert not source.verified_fixed and source.after_page is None
    assert len(vectors.searches) == 1 and embedding.calls[-1][0] == ("Маркировка",)
    assert {
        condition.key for condition in vectors.searches[0]["search_filter"].must
    } == {"kind", "embedding_identity"}
    feed.current.clear()
    assert (await search.execute(["Маркировка"]))[0].sources == ()


@pytest.mark.parametrize(
    "tag,target",
    [
        ("bad", ""),
        ("edited", "original"),
        ("edited", "revised"),
        ("edited", "both"),
        ("gold", ""),
    ],
)
async def test_source_preserves_learning_polarity_without_claiming_project_fixed(
    tag, target
):
    """Положительный пример не означает исправление; отрицательный не выдаётся как найденная ошибка."""
    _, _, _, index, search = services((trusted_example(tag=tag, target=target),))
    await index.execute()
    source = (await search.execute(["Шлейф"]))[0].sources[0]
    assert source.tag == tag and not source.verified_fixed
    assert source.learning_use == ("negative" if tag == "bad" or target else "positive")
    if source.learning_use == "negative":
        assert "Не повторяйте" in source.before_context and source.negative_target in {
            "original",
            "revised",
        }


@pytest.mark.parametrize(
    "corruption",
    ["identity", "kind", "score", "id", "target", "revision", "fingerprint"],
)
async def test_untrusted_vector_point_is_excluded(corruption):
    """Повреждённый scope, низкий score и чужой UUID не назначают источник E."""
    item = trusted_example()
    _, _, vectors, index, search = services((item,))
    await index.execute()
    record = next(iter(vectors.records.values()))
    payload, point_id, score = dict(record.payload), record.point_id, 0.9
    if corruption == "identity":
        payload["embedding_identity"] = "other"
    elif corruption == "kind":
        payload["kind"] = "legacy"
    elif corruption == "score":
        score = 0.64
    elif corruption == "id":
        point_id = "foreign"
    elif corruption == "target":
        payload["target"] = "original"
    elif corruption == "revision":
        payload["example_revision"] += 1
    else:
        payload["fingerprint"] = "f" * 64
    vectors.points = [VectorPoint(point_id, score, payload)]
    assert (await search.execute(["Шлейф"]))[0].sources == ()


async def test_owner_outage_has_no_fallback_to_vector_payload():
    """Сетевой сбой отличается от честного отсутствия результатов."""
    feed, _, _, index, search = services((trusted_example(),))
    await index.execute()
    feed.fail_verify = True
    with pytest.raises(ExperienceFeedError):
        await search.execute(["Шлейф"])


async def test_disabled_search_does_not_call_any_infrastructure():
    """Обычный анализ остаётся независим от нового экспериментального поиска."""
    feed, embedding, vectors, _, search = services()
    result = await replace(search, enabled=False).execute(["Шлейф", "Шлейф"])
    assert all(not value.sources for value in result)
    assert not feed.verify_calls and not embedding.calls and not vectors.searches


async def test_cursor_loop_is_a_failure_before_cleanup():
    """Зациклившийся сервис не вызывает бесконечный обход или удаление индекса."""
    item = trusted_example()
    _, _, vectors, index, _ = services((item,))

    class RepeatingFeed(MemoryFeed):
        """Моделирует нарушение ключевого курсора HTTP-владельцем."""

        async def page(self, *, after, limit):
            """Повторяет одну и ту же границу страницы."""
            return (), item.reference.example_id

    with pytest.raises(ExperienceFeedError, match="курсор"):
        await replace(index, source=RepeatingFeed()).execute()
    assert not vectors.deletes
