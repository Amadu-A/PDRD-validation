# tests/artifact_quality_support.py

"""Синтетический отчёт только для проверки контракта; не допуск рабочего E."""

from datetime import UTC, datetime
from hashlib import sha256


def report_for(version):
    """Создаёт контролируемые метрики; реальный отчёт выдаёт CLI evaluate."""
    if not isinstance(version, dict):
        from dataclasses import asdict

        version = asdict(version)
    return {
        "report_schema_version": 1,
        "approved": True,
        "version_id": str(version["id"]),
        "manifest_sha256": version["manifest_sha256"],
        "model": version["model"],
        "section_id": version["section_id"],
        "embedding_identity": version["embedding_identity"],
        "dimension": version["dimension"],
        "collection": version["collection"],
        "dataset_sha256": "f" * 64,
        "evaluation_document_sha256": [
            sha256(f"held-out-fixture-{i}".encode()).hexdigest() for i in range(5)
        ],
        "evaluated_at": datetime.now(UTC).isoformat(),
        "top_k": 5,
        "min_score": 0.25,
        "retrieval_cases": 20,
        "finding_documents": 5,
        "recall_at_k": 0.9,
        "forbidden_hits": 0,
        "baseline": {"precision": 0.8, "recall": 0.7, "false_positives": 2},
        "with_experience": {"precision": 0.9, "recall": 0.8, "false_positives": 1},
    }
