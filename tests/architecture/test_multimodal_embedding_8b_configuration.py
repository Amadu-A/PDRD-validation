# tests/architecture/test_multimodal_embedding_8b_configuration.py

"""Architecture guards TZ-6 multimodal 8B configuration."""

from pathlib import Path

PROJECT_ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

COMPOSE = PROJECT_ROOT / "compose.yaml"

MULTIMODAL_SETTINGS = (
    PROJECT_ROOT
    / "services"
    / "multimodal-embedding-service"
    / "src"
    / "pdrd_multimodal_embedding_service"
    / "settings.py"
)

MULTIMODAL_RUNTIME = (
    PROJECT_ROOT
    / "services"
    / "multimodal-embedding-service"
    / "src"
    / "pdrd_multimodal_embedding_service"
    / "runtime.py"
)

KNOWLEDGE_SETTINGS = (
    PROJECT_ROOT
    / "services"
    / "knowledge-service"
    / "src"
    / "pdrd_knowledge_service"
    / "core"
    / "settings.py"
)


def test_environment_activates_8b_4096_collection() -> None:
    """Default deploy больше не возвращается к 2B environment."""
    text = ENV_EXAMPLE.read_text(
        encoding="utf-8",
    )

    assert (
        "KNOWLEDGE_SERVICE_MULTIMODAL_EMBEDDING__MODEL="
        "Qwen/Qwen3-VL-Embedding-8B" in text
    )

    assert "KNOWLEDGE_SERVICE_MULTIMODAL_EMBEDDING__OUTPUT_DIMENSION=4096" in text

    assert (
        "KNOWLEDGE_SERVICE_QDRANT__MULTIMODAL_COLLECTION="
        "dva_multimodal_qwen3vl8b_v1" in text
    )

    assert "MULTIMODAL_EMBEDDING_MODEL__NAME=Qwen/Qwen3-VL-Embedding-8B" in text

    assert "MULTIMODAL_EMBEDDING_MODEL__OUTPUT_DIMENSION=4096" in text

    assert "MULTIMODAL_EMBEDDING_MODEL__MIN_FREE_RAM_GIB=20" in text


def test_python_defaults_match_8b_environment() -> None:
    """Python defaults и deploy environment используют один vector space."""
    multimodal_text = MULTIMODAL_SETTINGS.read_text(
        encoding="utf-8",
    )

    knowledge_text = KNOWLEDGE_SETTINGS.read_text(
        encoding="utf-8",
    )

    assert 'name: str = "Qwen/Qwen3-VL-Embedding-8B"' in multimodal_text

    assert "default=4096" in multimodal_text

    assert "min_free_ram_gib" in multimodal_text

    assert '"dva_multimodal_qwen3vl8b_v1"' in knowledge_text


def test_runtime_checks_ram_before_checkpoint_load() -> None:
    """RAM admission выполняется до SentenceTransformer checkpoint load."""
    text = MULTIMODAL_RUNTIME.read_text(
        encoding="utf-8",
    )

    ram_check = text.index(
        "self._require_system_ram_admission()",
    )

    model_import = text.index(
        '"sentence_transformers"',
    )

    model_construct = text.index(
        "model = model_class(",
    )

    assert ram_check < model_import

    assert ram_check < model_construct


def test_heavy_t_worker_remains_serialized() -> None:
    """TZ-6 не разрешает параллельные тяжёлые T jobs."""
    text = COMPOSE.read_text(
        encoding="utf-8",
    )

    worker_start = text.index(
        "technical-assignment-indexer:",
    )

    worker_end = text.index(
        "knowledge-service-tests:",
        worker_start,
    )

    worker = text[worker_start:worker_end]

    assert '"--concurrency=1"' in worker

    assert '"--prefetch-multiplier=1"' in worker

    assert '"--queues=pdrd.knowledge.technical-assignment"' in worker
