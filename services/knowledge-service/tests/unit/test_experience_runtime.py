# services/knowledge-service/tests/unit/test_experience_runtime.py

"""Операторский CLI E: отчёт с совместимостью, отдельный preview и выключенный рабочий поиск."""

import json
from argparse import Namespace
from uuid import UUID

import pytest
from pdrd_knowledge_service import experience_runtime as runtime
from pdrd_knowledge_service.application.ports.experience_versions import VersionIndexJob
from pdrd_knowledge_service.core.experience import ExperienceContainer
from pdrd_knowledge_service.core.settings import ExperienceIndexSettings, Settings
from pdrd_knowledge_service.infrastructure.experience_feed import parse_example

from tests.experience_index_support import signed, trusted_example

from .test_experience_quality import evaluation
from .test_trusted_experience import services


@pytest.mark.parametrize("use_version", [False, True])
async def test_evaluation_writes_report_with_dataset_hash_without_enabling_main_search(
    tmp_path, monkeypatch, capsys, use_version
):
    """Проверяется инструмент оценки на synthetic fixture, а не качество производственной VLM."""
    example = parse_example(signed({**trusted_example().data, "section_id": "СП 1:6"}))
    _, _, _, index, search = services((example,))
    await index.execute()
    monkeypatch.setattr(
        runtime,
        "build_experience_container",
        lambda settings, shadow: ExperienceContainer(index, search),
    )
    version = VersionIndexJob(
        UUID(int=100),
        index.collection,
        index.identity,
        index.dimension,
        "СП 1:6",
        (example,),
        "b" * 64,
    )

    async def prepare_version(settings, container, version_id):
        assert version_id == version.id
        return container, version

    monkeypatch.setattr(runtime, "prepare_version_search", prepare_version)
    cases, _, findings = evaluation()
    dataset, output = tmp_path / "dataset.json", tmp_path / "quality/report.json"
    dataset.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "split": "held_out_documents",
                "retrieval_cases": cases,
                "finding_cases": findings,
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        experience=ExperienceIndexSettings(key="private-key-" + "x" * 52),
    )
    await runtime.run(
        Namespace(
            command="evaluate",
            dataset=dataset,
            output=output,
            version=version.id if use_version else None,
        ),
        settings,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["approved"] and report["report_schema_version"] == 1
    assert (
        report["collection"] == index.collection
        and report["embedding_identity"] == index.identity
    )
    assert len(report["dataset_sha256"]) == 64 and report["top_k"] == search.top_k
    assert report["dimension"] == index.dimension
    assert report["model"] == settings.embedding_model
    assert report["evaluation_document_sha256"] == sorted(
        {case["source_sha256"] for case in (*cases, *findings)}
    )
    assert report["evaluated_at"].endswith("+00:00")
    if use_version:
        assert report["version_id"] == str(version.id)
        assert report["manifest_sha256"] == version.manifest_sha256
        assert report["section_id"] == version.section_id
    assert (
        not settings.search.experience_enabled
        and settings.experience.key.get_secret_value() not in capsys.readouterr().out
    )


async def test_preview_prints_tag_and_learning_use_without_writing_quality_report(
    tmp_path, monkeypatch, capsys
):
    """Preview помогает инженеру сверить примеры, но не открывает рабочий E."""
    _, _, _, index, search = services((trusted_example(tag="bad"),))
    await index.execute()
    monkeypatch.setattr(
        runtime,
        "build_experience_container",
        lambda settings, shadow: ExperienceContainer(index, search),
    )
    report = tmp_path / "missing-report.json"
    settings = Settings(
        _env_file=None,
        experience=ExperienceIndexSettings(key="x" * 64, quality_report=report),
    )
    await runtime.run(Namespace(command="preview", query=["Шлейф"]), settings)
    output = json.loads(capsys.readouterr().out)
    assert (
        output[0]["sources"][0]["tag"] == "bad"
        and output[0]["sources"][0]["learning_use"] == "negative"
    )
    assert not report.exists() and not settings.search.experience_enabled


async def test_sync_disabled_configuration_does_not_mutate_index(monkeypatch):
    """Оператор не может незаметно начать синхронизацию без профиля индекса."""
    _, _, vectors, index, search = services((trusted_example(),))
    monkeypatch.setattr(
        runtime,
        "build_experience_container",
        lambda settings, shadow: ExperienceContainer(index, search),
    )
    settings = Settings(
        _env_file=None, experience=ExperienceIndexSettings(key="x" * 64)
    )
    with pytest.raises(ValueError, match="выключена"):
        await runtime.run(Namespace(command="sync"), settings)
    assert not vectors.writes and not vectors.exists
