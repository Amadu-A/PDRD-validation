# services/analysis-service/src/pdrd_analysis_service/application/technical_assignment_schema.py

"""JSON Schema независимой проверки atomic requirements ТЗ."""

from typing import Any

from pdrd_analysis_service.application.json_schemas import (
    FINDING_SEVERITIES,
)

TECHNICAL_ASSIGNMENT_DECISION_STATUSES = (
    "not_applicable",
    "satisfied",
    "violated",
    "insufficient_evidence",
)

TECHNICAL_ASSIGNMENT_FINDING_STATUSES = (
    "violated",
    "insufficient_evidence",
)


def build_technical_assignment_check_schema(
    requirement_ids: tuple[
        str,
        ...,
    ],
) -> dict[str, Any]:
    """Строит compact exact-key schema для одного T validation batch."""
    if not requirement_ids:
        raise ValueError(
            "Для T-first проверки нужен хотя бы один requirement.",
        )

    if len(
        set(
            requirement_ids,
        )
    ) != len(
        requirement_ids,
    ):
        raise ValueError(
            "requirement_ids должны быть уникальными.",
        )

    decision_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {
                "type": "string",
                "enum": list(
                    TECHNICAL_ASSIGNMENT_DECISION_STATUSES,
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
        },
        "required": [
            "status",
            "confidence",
        ],
    }

    issue_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "requirement_id": {
                "type": "string",
                "enum": list(
                    requirement_ids,
                ),
            },
            "status": {
                "type": "string",
                "enum": list(
                    TECHNICAL_ASSIGNMENT_FINDING_STATUSES,
                ),
            },
            "severity": {
                "type": "string",
                "enum": list(
                    FINDING_SEVERITIES,
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
                "maxLength": 500,
            },
            "recommendation_draft": {
                "type": "string",
                "maxLength": 420,
            },
        },
        "required": [
            "requirement_id",
            "status",
            "severity",
            "comment",
            "evidence",
            "recommendation_draft",
        ],
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "object",
                "additionalProperties": False,
                "properties": dict.fromkeys(requirement_ids, decision_schema),
                "required": list(
                    requirement_ids,
                ),
            },
            "issues": {
                "type": "array",
                "items": issue_schema,
                "maxItems": len(
                    requirement_ids,
                ),
            },
        },
        "required": [
            "decisions",
            "issues",
        ],
    }
