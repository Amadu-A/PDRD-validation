# services/analysis-service/src/pdrd_analysis_service/application/json_schemas.py

"""Схемы структурированных ответов визуальной модели для этапов анализа."""

from typing import Any

FINDING_CATEGORIES = (
    "normative_control",
    "equipment",
    "scheme_logic",
    "marking",
    "completeness",
    "optimization",
    "customer_requirements",
    "document_consistency",
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


def build_page_facts_schema(
    max_facts: int = 12, max_equipment: int = 0
) -> dict[str, Any]:
    """Возвращает лёгкую схему листа; D-факты добавляет только при положительном лимите."""
    schema = {
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

    if max_facts <= 0:
        return _add_equipment_schema(schema, max_equipment)

    text_fields = (
        "kind",
        "subject_type",
        "subject_name",
        "identifier",
        "property_type",
        "property_name",
        "value_raw",
        "unit_raw",
        "scope_system",
        "scope_location",
        "scope_segment",
        "scope_operating_mode",
        "scope_condition",
        "relation",
        "table_title",
        "table_id",
        "row_label",
        "column_label",
        "continuation_marker",
        "evidence_text",
    )
    fact_properties = {
        name: {"type": "string", "maxLength": 220} for name in text_fields
    }
    fact_properties["visual_regions"] = build_finding_visual_regions_schema()
    fact_properties["visual_regions"]["maxItems"] = 2
    schema["properties"]["document_facts"] = {
        "type": "array",
        "maxItems": max_facts,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": fact_properties,
            "required": [*text_fields, "visual_regions"],
        },
    }
    schema["required"].append("document_facts")
    return _add_equipment_schema(schema, max_equipment)


def _add_equipment_schema(schema: dict[str, Any], limit: int) -> dict[str, Any]:
    """Условно добавляет идентичность и проектные параметры оборудования."""
    if limit <= 0:
        return schema

    text = {"type": "string", "maxLength": 220}
    parameter_fields = (
        "property_name",
        "value_raw",
        "unit_raw",
        "role",
        "current_type",
        "mode",
        "evidence_text",
    )
    parameter = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **dict.fromkeys(parameter_fields, text),
            "phase": text,
            "configuration": text,
            "condition": text,
        },
        "required": list(parameter_fields),
    }
    identity_fields = (
        "manufacturer",
        "model",
        "variant",
        "article",
        "equipment_type",
        "object_ref",
        "evidence_text",
        "status",
    )
    properties = dict.fromkeys(identity_fields, text)
    properties["status"] = {
        "type": "string",
        "enum": ["resolved", "needs_review"],
    }
    properties["confidence"] = {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
    properties["visual_regions"] = build_finding_visual_regions_schema()
    properties["parameters"] = {
        "type": "array",
        "maxItems": 8,
        "items": parameter,
    }
    schema["properties"]["equipment_identities"] = {
        "type": "array",
        "maxItems": limit,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": [
                *identity_fields,
                "confidence",
                "visual_regions",
                "parameters",
            ],
        },
    }
    schema["required"].append("equipment_identities")
    return schema


def build_finding_visual_regions_schema() -> dict[str, Any]:
    """Возвращает схему областей доказательств одного замечания визуальной модели."""
    return {
        "type": "array",
        "maxItems": 4,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "x_min": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "y_min": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "x_max": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "y_max": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000,
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "label": {
                    "type": "string",
                    "maxLength": 120,
                },
            },
            "required": [
                "x_min",
                "y_min",
                "x_max",
                "y_max",
                "confidence",
                "label",
            ],
        },
    }


def _source_ids_schema(
    source_ids: tuple[
        str,
        ...,
    ],
) -> dict[str, Any]:
    """Строит array schema только для реально переданных source IDs."""
    if not source_ids:
        return {
            "type": "array",
            "maxItems": 0,
            "items": {
                "type": "string",
            },
        }

    return {
        "type": "array",
        "maxItems": 3,
        "items": {
            "type": "string",
            "enum": list(
                source_ids,
            ),
        },
    }


def build_normative_check_schema(
    *,
    source_ids: tuple[str, ...],
    max_issues: int,
    technical_assignment_source_ids: tuple[
        str,
        ...,
    ] = (),
    user_package_source_ids: tuple[
        str,
        ...,
    ] = (),
    document_context_source_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Возвращает схему проверки кандидатов N/T/U с D только при наличии источников."""
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 200,
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
                            "maxLength": 160,
                        },
                        "evidence": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 180,
                        },
                        "object_ref": {
                            "type": "string",
                            "maxLength": 80,
                        },
                        "recommendation_draft": {
                            "type": "string",
                            "maxLength": 0,
                            "enum": [
                                "",
                            ],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "visual_regions": (build_finding_visual_regions_schema()),
                        "normative_source_ids": (
                            _source_ids_schema(
                                source_ids,
                            )
                        ),
                        "technical_assignment_source_ids": (
                            _source_ids_schema(
                                technical_assignment_source_ids,
                            )
                        ),
                        "document_context_source_ids": _source_ids_schema(
                            document_context_source_ids
                        ),
                        "user_package_source_ids": (
                            _source_ids_schema(
                                user_package_source_ids,
                            )
                        ),
                    },
                    "required": [
                        "category",
                        "severity",
                        "status",
                        "comment",
                        "evidence",
                        "object_ref",
                        "recommendation_draft",
                        "confidence",
                        "visual_regions",
                        "normative_source_ids",
                        "technical_assignment_source_ids",
                        "user_package_source_ids",
                        "document_context_source_ids",
                    ],
                },
            },
        },
        "required": [
            "summary",
            "violations",
        ],
    }

    if not document_context_source_ids:
        finding = schema["properties"]["violations"]["items"]
        del finding["properties"]["document_context_source_ids"]
        finding["required"].remove("document_context_source_ids")
    return schema


def build_finalization_schema(
    finding_ids: tuple[str, ...],
    normative_source_ids: tuple[
        str,
        ...,
    ] = (),
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
                        "decision": {
                            "type": "string",
                            "enum": [
                                "keep",
                                "reject",
                            ],
                        },
                        "rejection_reason": {
                            "type": "string",
                            "maxLength": 300,
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
                        "normative_source_ids": (
                            _source_ids_schema(
                                normative_source_ids,
                            )
                        ),
                    },
                    "required": [
                        "finding_id",
                        "decision",
                        "rejection_reason",
                        "comment",
                        "recommendation",
                        "experience_source_ids",
                        "normative_source_ids",
                    ],
                },
            },
        },
        "required": [
            "summary",
            "findings",
        ],
    }
