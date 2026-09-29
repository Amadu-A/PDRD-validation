# services/knowledge-service/tests/unit/test_experience_quality.py

"""Расчёт качества E и закрытый по умолчанию запуск: тестовый набор не является разрешением."""

import copy
import json
from uuid import UUID

import pytest
from pdrd_knowledge_service.core.experience import (
    build_experience_container,
    require_quality_report,
)
from pdrd_knowledge_service.core.settings import (
    ExperienceIndexSettings,
    SearchSettings,
    Settings,
)
from pdrd_knowledge_service.domain.experience_quality import quality_report


def evaluation():
    """Синтетические данные только для проверки расчёта и условий, не для рабочего отчёта."""
    relevant = str(UUID(int=1))
    cases = [
        {
            "document_id": str(UUID(int=100 + index)),
            "source_sha256": f"{100 + index:064x}",
            "query": "Маркировка",
            "relevant_example_ids": [relevant],
            "forbidden_example_ids": [],
        }
        for index in range(20)
    ]
    findings = [
        {
            "document_id": str(UUID(int=100 + index)),
            "source_sha256": f"{100 + index:064x}",
            "expected": ["true"],
            "baseline": ["true", "false"],
            "with_experience": ["true"],
        }
        for index in range(5)
    ]
    return cases, [[relevant] for _ in cases], findings


def report():
    """Метаданные явно привязывают оценку к модели, коллекции и параметрам поиска."""
    cases, retrieved, findings = evaluation()
    data = quality_report(
        cases=cases,
        retrieved=retrieved,
        finding_cases=findings,
        indexed_documents=set(),
    )
    return {
        **data,
        "dataset_sha256": "a" * 64,
        "embedding_identity": "identity",
        "collection": "trusted",
        "top_k": 3,
        "min_score": 0.65,
    }


def test_quality_metrics_count_false_findings_and_require_improvement():
    """Precision растёт при удалении ложного замечания без потери истинного."""
    data = report()
    assert data["approved"] is True
    assert data["baseline"] == {"precision": 0.5, "recall": 1.0, "false_positives": 5}
    assert data["with_experience"] == {
        "precision": 1.0,
        "recall": 1.0,
        "false_positives": 0,
    }
    cases, retrieved, findings = evaluation()
    for item in findings:
        item["with_experience"] = item["baseline"]
    assert not quality_report(
        cases=cases,
        retrieved=retrieved,
        finding_cases=findings,
        indexed_documents=set(),
    )["approved"]


def test_same_pdf_with_another_document_id_is_not_a_held_out_case():
    """Повторная загрузка PDF не скрывает утечку обучающего источника."""
    cases, retrieved, findings = evaluation()
    with pytest.raises(ValueError, match="другим document_id"):
        quality_report(
            cases=cases,
            retrieved=retrieved,
            finding_cases=findings,
            indexed_documents=set(),
            indexed_source_hashes={cases[0]["source_sha256"]},
        )


@pytest.mark.parametrize(
    "problem", ["small", "forbidden", "poor_recall", "lost_true", "more_false"]
)
def test_quality_gate_rejects_inadequate_or_worse_experiment(problem):
    """Одна высокая retrieval-метрика не компенсирует ухудшение инженерного результата."""
    cases, retrieved, findings = evaluation()
    if problem == "small":
        cases, retrieved = cases[:19], retrieved[:19]
    elif problem == "forbidden":
        cases[0]["forbidden_example_ids"] = [str(UUID(int=2))]
        retrieved[0].append(str(UUID(int=2)))
    elif problem == "poor_recall":
        retrieved[:5] = [[] for _ in range(5)]
    elif problem == "lost_true":
        findings[0]["with_experience"] = []
    else:
        for item in findings:
            item["with_experience"] = ["true", "false1", "false2"]
    assert not quality_report(
        cases=cases,
        retrieved=retrieved,
        finding_cases=findings,
        indexed_documents=set(),
    )["approved"]


@pytest.mark.parametrize(
    "problem",
    ["leak", "duplicate_query", "duplicate_document", "contradiction", "bad_labels"],
)
def test_invalid_dataset_cannot_inflate_or_leak_evaluation(problem):
    """Отложенный документ отсутствует в E; дубликаты не увеличивают объём оценки."""
    cases, retrieved, findings = evaluation()
    indexed = set()
    if problem == "leak":
        indexed = {cases[0]["document_id"]}
    elif problem == "duplicate_query":
        cases[1] = copy.deepcopy(cases[0])
    elif problem == "duplicate_document":
        findings[1] = copy.deepcopy(findings[0])
    elif problem == "contradiction":
        cases[0]["forbidden_example_ids"] = cases[0]["relevant_example_ids"]
    else:
        findings[0]["baseline"] = "true"
    with pytest.raises(ValueError):
        quality_report(
            cases=cases,
            retrieved=retrieved,
            finding_cases=findings,
            indexed_documents=indexed,
        )


@pytest.mark.parametrize(
    "problem",
    [
        "identity",
        "collection",
        "not_approved",
        "top_k",
        "min_score",
        "nan",
        "count_bool",
        "hash",
        "regression",
    ],
)
def test_runtime_cannot_start_with_incompatible_or_invalid_report(tmp_path, problem):
    """Даже approved=true не отменяет проверку совместимости и конечных метрик."""
    data = report()
    if problem == "identity":
        data["embedding_identity"] = "other"
    elif problem == "collection":
        data["collection"] = "other"
    elif problem == "not_approved":
        data["approved"] = False
    elif problem == "top_k":
        data["top_k"] = 4
    elif problem == "min_score":
        data["min_score"] = 0.5
    elif problem == "nan":
        data["with_experience"]["recall"] = float("nan")
    elif problem == "count_bool":
        data["retrieval_cases"] = True
    elif problem == "hash":
        data["dataset_sha256"] = "z" * 64
    else:
        data["with_experience"]["false_positives"] = 10
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="отложенных"):
        require_quality_report(
            path, identity="identity", collection="trusted", top_k=3, min_score=0.65
        )


def test_valid_report_acceptance_and_missing_report_rejection(tmp_path):
    """Отчёт открывает только соответствующую конфигурацию; отсутствие закрывает запуск."""
    path = tmp_path / "report.json"
    with pytest.raises(ValueError):
        require_quality_report(
            path, identity="identity", collection="trusted", top_k=3, min_score=0.65
        )
    path.write_text(json.dumps(report()), encoding="utf-8")
    require_quality_report(
        path, identity="identity", collection="trusted", top_k=3, min_score=0.65
    )


def test_main_composition_uses_registry_admission_without_enabling_shadow_in_production(
    tmp_path,
):
    """Локальный отчёт не открывает API; рабочий поиск проверяет допуск владельца каждый запрос."""
    settings = Settings(
        _env_file=None,
        experience=ExperienceIndexSettings(
            key="k" * 64, quality_report=tmp_path / "missing.json"
        ),
    )
    assert not settings.search.experience_enabled
    assert not build_experience_container(settings).search.enabled
    assert build_experience_container(settings, shadow=True).search.enabled
    settings.search = SearchSettings(experience_enabled=True)
    search = build_experience_container(settings).search
    assert search.enabled and search.search.collection == ""
    assert not settings.experience.quality_report.exists()


def test_readiness_does_not_require_legacy_collection_for_applied_versions(
    tmp_path, monkeypatch
):
    """Версии разделов выбирают свои коллекции при поиске; готовность API не требует legacy E."""
    from pdrd_knowledge_service.core import container as composition

    path = tmp_path / "report.json"
    settings = Settings(
        _env_file=None,
        search=SearchSettings(experience_enabled=True),
        experience=ExperienceIndexSettings(key="k" * 64, quality_report=path),
    )
    collection = settings.embedding_index_plan.experience_target + "_human_review_v1"
    data = report()
    data.update(
        embedding_identity=settings.embedding_identity.fingerprint,
        collection=collection,
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(composition, "get_settings", lambda: settings)
    container = composition.build_container()
    assert container.check_readiness.experience_collection is None
    assert container.search_experience.search.collection == ""
