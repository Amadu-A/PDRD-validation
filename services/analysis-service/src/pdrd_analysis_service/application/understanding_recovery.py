# services/analysis-service/src/pdrd_analysis_service/application/understanding_recovery.py

"""Безопасное продолжение после переполнения: никаких восстановительных VLM-запросов."""


def limited_understanding_payload(heuristic_page_type: str) -> dict:
    """Оставляет визуальные сведения пустыми; программные факты добавляет основной сценарий."""
    return {
        "discipline": "",
        "page_type": heuristic_page_type,
        "summary": "",
        "objects": [],
        "connections": [],
        "labels": [],
        "normative_queries": [],
    }
