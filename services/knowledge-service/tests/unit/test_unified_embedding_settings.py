# services/knowledge-service/tests/unit/test_unified_embedding_settings.py

"""Unit tests единого embedding identity."""

from pdrd_knowledge_service.core.settings import (
    Settings,
)


def test_text_and_multimodal_settings_are_same_model() -> None:
    """N/T/U/E/PZ обязаны использовать один vector space."""
    settings = Settings(
        embedding_model="Qwen/Test-Embedding",
        embedding_dimension=3072,
        embedding_schema_version=7,
    )

    assert settings.embedding.model == "Qwen/Test-Embedding"

    assert settings.multimodal_embedding.model == "Qwen/Test-Embedding"

    assert settings.embedding.output_dimension == 3072

    assert settings.multimodal_embedding.output_dimension == 3072


def test_model_change_changes_all_physical_collection_targets() -> None:
    """Fingerprint автоматически создаёт новый blue/green target."""
    first = Settings(
        embedding_model="model-a",
        embedding_dimension=4096,
    )

    second = Settings(
        embedding_model="model-b",
        embedding_dimension=4096,
    )

    assert (
        first.embedding_index_plan.catalog_target
        != second.embedding_index_plan.catalog_target
    )

    assert (
        first.embedding_index_plan.technical_assignment_target
        != second.embedding_index_plan.technical_assignment_target
    )

    assert (
        first.embedding_index_plan.experience_target
        != second.embedding_index_plan.experience_target
    )

    assert (
        first.qdrant.normative_collection
        == second.qdrant.normative_collection
        == "dva_catalog_active"
    )
