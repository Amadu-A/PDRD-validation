# tests/architecture/test_experience_index_boundaries.py

"""Архитектурные границы E: отдельный индекс, закрытый канал, прежние модели и quality gate."""

import ast
import runpy
from pathlib import Path

import pytest
import yaml
from pdrd_experience_service.core.settings import Settings as ExperienceSettings
from pdrd_knowledge_service.core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_e_domain_and_application_do_not_cross_service_or_infrastructure_boundary():
    """SQL/PDF/GPU/HTTP не импортируются в ядро и не берутся из чужого Python-пакета."""
    for service, package in [
        ("knowledge-service", "pdrd_knowledge_service"),
        ("experience-service", "pdrd_experience_service"),
    ]:
        directory = ROOT / "services" / service / "src" / package
        for layer in ("domain", "application"):
            for path in (directory / layer).rglob("*.py"):
                if not any(
                    part in path.stem
                    for part in ("experience", "index_feed", "index_projection")
                ):
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imports = [
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                ]
                imports += [
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                ]
                assert not any(
                    module.split(".")[0]
                    in {"fitz", "httpx", "sqlalchemy", "fastapi", "pydantic", "PIL"}
                    for module in imports
                ), path
                assert not any(
                    ".infrastructure" in module or ".transport" in module
                    for module in imports
                ), path
                assert not any(
                    module.startswith("pdrd_") and not module.startswith(package + ".")
                    for module in imports
                ), path


def test_compose_indexer_has_no_public_port_and_frontend_has_no_index_credentials():
    """Отдельный read-only канал остаётся между сервисами; runtime использует существующую shared-сеть."""
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    indexer = services["experience-indexer"]
    assert indexer["profiles"] == ["experience-index"] and "ports" not in indexer
    assert indexer["build"]["context"] == "./services/knowledge-service"
    assert "ai-shared" in indexer["networks"]
    assert (
        indexer["environment"]["KNOWLEDGE_SERVICE_EXPERIENCE__INDEX_ENABLED"] == "true"
    )
    assert "experience_index_quality:/data/experience-quality" in indexer["volumes"]
    for frontend in ("frontend", "review-frontend"):
        assert "env_file" not in services[frontend]
        assert not any(
            "INDEX" in key or "EXPERIENCE" in key
            for key in services[frontend]["environment"]
        )
    assert (
        services["experience-service"]["environment"]["EXPERIENCE_SERVICE_INDEX_KEY"]
        == "${PDRD_EXPERIENCE_INDEX_KEY:-}"
    )


def test_index_collection_has_separate_kind_and_embedding_identity_without_changing_legacy_alias():
    """Human Review не смешивается с прежними before/after-точками при обновлении модели."""
    settings = Settings(_env_file=None)
    module = (
        ROOT
        / "services/knowledge-service/src/pdrd_knowledge_service/core/experience.py"
    ).read_text(encoding="utf-8")
    assert 'experience_target + "_human_review_v1"' in module
    assert '"pdrd_experience_service"' not in module
    assert (
        not settings.search.experience_enabled and not settings.experience.index_enabled
    )


@pytest.mark.parametrize("index_key", ["short", "!" * 64, "review-key-" + "r" * 54])
def test_index_key_is_distinct_from_review_authority(index_key):
    """Ключ чтения E не заменяет право инженерной записи."""
    with pytest.raises(ValueError):
        ExperienceSettings(
            _env_file=None,
            enabled=True,
            index_key=index_key,
            internal_key="review-key-" + "r" * 54,
            database={"password": "test-secret"},
        )


def test_prepare_creates_key_idempotently_preserves_other_settings_and_keeps_production_off(
    tmp_path, capsys
):
    """Подготовка не печатает секрет и не переписывает остальные параметры .env."""
    prepare = runpy.run_path(str(ROOT / "ops/prepare-experience-index.py"))["prepare"]
    path = tmp_path / ".env"
    original = "# комментарий\nOTHER_SECRET=preserve-me\nKNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=true\n"
    path.write_text(original, encoding="utf-8")
    prepare(path)
    first = path.read_text(encoding="utf-8")
    prepare(path)
    assert path.read_text(encoding="utf-8") == first
    assert "OTHER_SECRET=preserve-me" in first and "# комментарий" in first
    assert "KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=false" in first
    key = next(
        line.split("=", 1)[1]
        for line in first.splitlines()
        if line.startswith("PDRD_EXPERIENCE_INDEX_KEY=")
    )
    assert len(key) == 64 and key not in capsys.readouterr().out


def test_prepare_rejects_duplicate_setting_without_changing_file(tmp_path):
    """Ошибка конфигурации не приводит к частичной замене секретов."""
    prepare = runpy.run_path(str(ROOT / "ops/prepare-experience-index.py"))["prepare"]
    path = tmp_path / ".env"
    original = "PDRD_EXPERIENCE_INDEX_KEY=\nPDRD_EXPERIENCE_INDEX_KEY=\n"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError):
        prepare(path)
    assert path.read_text(encoding="utf-8") == original


def test_prepare_does_not_delete_preexisting_temporary_file(tmp_path):
    """Неудачный O_EXCL оставляет чужой/предыдущий временный файл нетронутым."""
    prepare = runpy.run_path(str(ROOT / "ops/prepare-experience-index.py"))["prepare"]
    path = tmp_path / ".env"
    path.write_text("OTHER=preserved\n", encoding="utf-8")
    temporary = path.with_name(".env.experience-index.tmp")
    temporary.write_text("previous-operation", encoding="utf-8")
    with pytest.raises(FileExistsError):
        prepare(path)
    assert temporary.read_text(encoding="utf-8") == "previous-operation"
    assert path.read_text(encoding="utf-8") == "OTHER=preserved\n"


def test_linux_startup_builds_and_migrates_before_indexer_and_disables_e_by_default():
    """Штатный запуск собирает образы, применяет миграции и включает индексатор по профилю."""
    startup = (ROOT / "scripts/up.sh").read_text(encoding="utf-8")
    defaults = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert startup.index("docker compose build") < startup.index(
        "=== Database migrations ==="
    )
    assert startup.index("=== Database migrations ===") < startup.index(
        "docker compose up -d --remove-orphans"
    )
    assert 'profile_enabled "experience-index"' in startup
    assert "KNOWLEDGE_SERVICE_SEARCH__EXPERIENCE_ENABLED=false" in defaults
    assert "experience-indexer:" in compose
    assert "down -v" not in startup


def test_browser_cannot_enable_shadow_search_with_request_parameter():
    """Preview доступен операторскому CLI; HTTP-контракт содержит только queries."""
    from pdrd_knowledge_service.transport.http.schemas.search import (
        ExperienceSearchRequest,
    )

    with pytest.raises(ValueError):
        ExperienceSearchRequest.model_validate({"queries": ["Шлейф"], "shadow": True})
