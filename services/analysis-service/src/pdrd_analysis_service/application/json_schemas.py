# services/analysis-service/src/pdrd_analysis_service/application/json_schemas.py

"""JSON Schema для structured VLM pipeline."""

from typing import Any

FINDING_CATEGORIES = (
    "normative_control",
    "equipment",
    "scheme_logic",
    "marking",
    "completeness",
    "optimization",
    "customer_requirements",
    "other",
)

FINDING_SEVERITIES = (
    "info",
    "warning",
    "error",
)

FINDING_STATUSES = (
    "confirmed",
    "needs_review",
)


def build_page_facts_schema() -> dict[str, Any]:
    """Возвращает schema понимания листа."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "discipline": {
                "type": "string",
                "maxLength": 100,
            },
            "page_type": {
                "type": "string",
                "maxLength": 100,
            },
            "summary": {
                "type": "string",
                "maxLength": 600,
            },
            "objects": {
                "type": "array",
                "maxItems": 15,
                "items": {
                    "type": "string",
                    "maxLength": 200,
                },
            },
            "connections": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "string",
                    "maxLength": 250,
                },
            },
            "labels": {
                "type": "array",
                "maxItems": 15,
                "items": {
                    "type": "string",
                    "maxLength": 160,
                },
            },
            "normative_queries": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "string",
                    "maxLength": 240,
                },
            },
        },
        "required": [
            "discipline",
            "page_type",
            "summary",
            "objects",
            "connections",
            "labels",
            "normative_queries",
        ],
    }


def build_normative_check_schema(
    *,
    source_ids: tuple[str, ...],
    max_issues: int,
) -> dict[str, Any]:
    """Возвращает schema нормативной проверки."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 400,
            },
            "violations": {
                "type": "array",
                "maxItems": max_issues,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": list(
                                FINDING_CATEGORIES,
                            ),
                        },
                        "severity": {
                            "type": "string",
                            "enum": list(
                                FINDING_SEVERITIES,
                            ),
                        },
                        "status": {
                            "type": "string",
                            "enum": list(
                                FINDING_STATUSES,
                            ),
                        },
                        "comment": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 420,
                        },
                        "evidence": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 420,
                        },
                        "recommendation_draft": {
                            "type": "string",
                            "maxLength": 420,
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "normative_source_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {
                                "type": "string",
                                "enum": list(
                                    source_ids,
                                ),
                            },
                        },
                    },
                    "required": [
                        "category",
                        "severity",
                        "status",
                        "comment",
                        "evidence",
                        "recommendation_draft",
                        "confidence",
                        "normative_source_ids",
                    ],
                },
            },
        },
        "required": [
            "summary",
            "violations",
        ],
    }


def build_finalization_schema(
    finding_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Возвращает schema финализации одного batch."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 300,
            },
            "findings": {
                "type": "array",
                "minItems": len(
                    finding_ids,
                ),
                "maxItems": len(
                    finding_ids,
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {
                            "type": "string",
                            "enum": list(
                                finding_ids,
                            ),
                        },
                        "comment": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 350,
                        },
                        "recommendation": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 400,
                        },
                        "experience_source_ids": {
                            "type": "array",
                            "maxItems": 2,
                            "items": {
                                "type": "string",
                            },
                        },
                    },
                    "required": [
                        "finding_id",
                        "comment",
                        "recommendation",
                        "experience_source_ids",
                    ],
                },
            },
        },
        "required": [
            "summary",
            "findings",
        ],
    }
