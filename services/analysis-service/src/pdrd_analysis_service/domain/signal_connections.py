# services/analysis-service/src/pdrd_analysis_service/domain/signal_connections.py

"""Программный граф явно прочитанных соединений без электрических догадок."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass

from pdrd_analysis_service.domain.design_decisions import DecisionRegistry, stable_id
from pdrd_analysis_service.domain.document_context import (
    LocatedDocumentFact,
    normalize_label,
    scope_key,
)
from pdrd_analysis_service.domain.engineering_calculations import (
    comparison_tolerance,
    interval,
    scalar,
)
from pdrd_analysis_service.domain.engineering_checks import (
    EngineeringCheck,
    program_evidence_eligible,
)


@dataclass(frozen=True, slots=True)
class SignalConnection:
    """Направленная связь с исходными адресами, условиями и доказательством."""

    connection_id: str
    source: str
    target: str
    scope: tuple[str, ...]
    evidence_id: str
    grounding: str
    source_attributes: tuple[LocatedDocumentFact, ...]
    target_attributes: tuple[LocatedDocumentFact, ...]


@dataclass(frozen=True, slots=True)
class SignalGraph:
    """Граф одного документа; адрес задаётся явно как шкаф/аппарат:клемма."""

    document_id: str
    document_context_enabled: bool
    connections: tuple[SignalConnection, ...]
    unresolved_evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict:
        """Сохраняет все связи и пробелы, не назначая им выдуманные характеристики."""
        return asdict(self)

    def trace(self, source: str, target: str, *, scope: tuple[str, ...]) -> dict:
        """Прослеживает только одну область; найденный путь не доказывает совместимость."""
        if not self.document_context_enabled:
            return {
                "status": "not_evaluable",
                "reason_code": "document_context_disabled",
                "path": [],
                "missing_data": ["document_context"],
            }
        start, finish = normalize_label(source), normalize_label(target)
        adjacency: dict[str, list[SignalConnection]] = {}
        for connection in self.connections:
            if connection.scope == scope:
                adjacency.setdefault(normalize_label(connection.source), []).append(
                    connection
                )
        queue = deque([start])
        previous: dict[str, tuple[str, SignalConnection] | None] = {start: None}
        while queue:
            node = queue.popleft()
            if node == finish and node != start:
                path = []
                while previous[node] is not None:
                    node, edge = previous[node]
                    path.append(edge)
                path.reverse()
                return {
                    "status": "not_evaluable",
                    "reason_code": "electrical_compatibility_not_checked",
                    "path": [asdict(item) for item in path],
                    "missing_data": [
                        "electrical_characteristics",
                        "functional_requirements",
                    ],
                }
            for edge in adjacency.get(node, ()):
                neighbor = normalize_label(edge.target)
                if neighbor not in previous and edge.grounding != "ambiguous":
                    previous[neighbor] = (node, edge)
                    queue.append(neighbor)
        return {
            "status": "not_evaluable",
            "reason_code": "connection_path_not_observed",
            "path": [],
            "missing_data": ["external_connections_or_intermediate_links"],
        }


def build_signal_graph(registry: DecisionRegistry) -> SignalGraph:
    """Читает connected_to/feeds из DocumentFact; сходство обозначений не создаёт ребро."""
    connections, unresolved = [], []
    attributes: dict[tuple, list[LocatedDocumentFact]] = {}
    for evidence in registry.evidence:
        item = evidence.observation
        if item.fact.relation not in {"connected_to", "connects_to", "feeds"}:
            key = (normalize_label(item.fact.identifier), scope_key(item.fact)[:6])
            attributes.setdefault(key, []).append(item)
    for evidence in registry.evidence:
        located = evidence.observation
        fact = located.fact
        if fact.relation not in {"connected_to", "connects_to", "feeds"}:
            continue
        # Короткие XT9 и 1XT3 не определяют шкаф; связь остаётся непроверенной.
        if not _qualified(fact.identifier) or not _qualified(fact.value_raw):
            unresolved.append(evidence.evidence_id)
            continue
        connections.append(
            SignalConnection(
                stable_id("connection-", (registry.document_id, located.fact_id)),
                fact.identifier,
                fact.value_raw,
                scope_key(fact)[:6],
                evidence.evidence_id,
                evidence.grounding,
                tuple(
                    attributes.get(
                        (normalize_label(fact.identifier), scope_key(fact)[:6]), ()
                    )
                ),
                tuple(
                    attributes.get(
                        (normalize_label(fact.value_raw), scope_key(fact)[:6]), ()
                    )
                ),
            )
        )
    return SignalGraph(
        registry.document_id,
        registry.document_context_enabled,
        tuple(connections),
        tuple(unresolved),
    )


def _qualified(value: str) -> bool:
    """Требует отдельного непустого адреса шкафа и элемента, без восстановления по названию."""
    parts = value.split("/")
    return len(parts) == 2 and all(part.strip() for part in parts)


def check_signal_connections(
    registry: DecisionRegistry,
) -> tuple[EngineeringCheck, ...]:
    """Сравнивает только соседние концы доказанного ребра, сохраняя релейные разрывы."""
    graph = build_signal_graph(registry)
    if not registry.document_context_enabled:
        return ()
    facts = {item.evidence_id: item for item in registry.evidence}
    results = []
    for edge in graph.connections:
        source = _unambiguous_attributes(edge.source_attributes, facts)
        target = _unambiguous_attributes(edge.target_attributes, facts)
        observations = (
            facts[edge.evidence_id].observation,
            *source.values(),
            *target.values(),
        )
        base = {
            "rule": "signal_compatibility",
            "object_key": edge.source + "→" + edge.target,
            "property_key": "electrical_compatibility",
            "observations": observations,
        }
        if edge.grounding == "ambiguous":
            results.append(
                EngineeringCheck(
                    status="not_evaluable",
                    reason_code="connection_not_grounded",
                    missing_data=("source_connection",),
                    **base,
                )
            )
            continue
        results.extend(_functional_checks(edge, source, target, observations))
        dry = source.get("signal_type")
        is_dry = dry is not None and normalize_label(dry.fact.value_raw) in {
            "dry_contact",
            "сухой контакт",
        }
        voltage = source.get("feeding_voltage" if is_dry else "output_voltage")
        kind = source.get("feeding_current_type" if is_dry else "output_current_type")
        allowed, receiver_kind = (
            target.get("allowed_input_voltage"),
            target.get("input_current_type"),
        )
        if not all((voltage, kind, allowed, receiver_kind)):
            results.append(
                EngineeringCheck(
                    status="not_evaluable",
                    reason_code="dry_contact_supply_not_observed"
                    if is_dry and not voltage
                    else "electrical_characteristics_incomplete",
                    missing_data=tuple(
                        name
                        for name, value in (
                            (
                                "feeding_voltage" if is_dry else "output_voltage",
                                voltage,
                            ),
                            ("source_current_type", kind),
                            ("allowed_input_voltage", allowed),
                            ("input_current_type", receiver_kind),
                        )
                        if not value
                    ),
                    **base,
                )
            )
            continue
        output, limits = scalar(voltage.fact), interval(allowed.fact)
        if not output or not limits or output.dimension != limits[0].dimension:
            results.append(
                EngineeringCheck(
                    status="not_evaluable",
                    reason_code="electrical_range_not_established",
                    missing_data=("explicit_voltage_range",),
                    **base,
                )
            )
            continue
        types = [normalize_label(item.fact.value_raw) for item in (kind, receiver_kind)]
        if (
            any(value not in {"ac", "dc", "ac/dc"} for value in types)
            or types[0] == "ac/dc"
        ):
            results.append(
                EngineeringCheck(
                    status="not_evaluable",
                    reason_code="current_type_ambiguous",
                    missing_data=("explicit_AC_DC",),
                    **base,
                )
            )
            continue
        tolerance = comparison_tolerance(output, *limits)
        mismatch = (
            (types[0] != types[1] and types[1] != "ac/dc")
            or output.value < limits[0].value - tolerance
            or output.value > limits[1].value + tolerance
        )
        results.append(
            EngineeringCheck(
                status="confirmed" if mismatch else "no_finding",
                reason_code="direct_signal_incompatible"
                if mismatch
                else "direct_signal_values_compatible",
                calculation={
                    "output": str(output.value),
                    "input_low": str(limits[0].value),
                    "input_high": str(limits[1].value),
                    "source_type": types[0],
                    "target_type": types[1],
                    "tolerance": str(tolerance),
                },
                **base,
            )
        )
    return tuple(results)


def _functional_checks(edge, source, target, observations):
    """Не смешивает команду, состояние, защитное действие и электрические характеристики."""
    results = []
    for name in ("signal_function", "contact_state", "safe_state", "control_mode"):
        left, right = source.get(name), target.get(name)
        if left and right:
            equal = normalize_label(left.fact.value_raw) == normalize_label(
                right.fact.value_raw
            )
            results.append(
                EngineeringCheck(
                    "signal_function",
                    "no_finding" if equal else "needs_review",
                    edge.source + "→" + edge.target,
                    name,
                    observations,
                    "signal_semantics_equal"
                    if equal
                    else "signal_semantics_require_review",
                    missing_data=() if equal else ("verified_functional_logic",),
                )
            )
    return results


def _unambiguous_attributes(items, evidence):
    """Противоречащие атрибуты не разрешаются выбором последней записи."""
    groups = {}
    for item in items:
        if program_evidence_eligible(evidence[item.fact_id]):
            groups.setdefault(item.fact.property_type, []).append(item)
    return {
        kind: values[0]
        for kind, values in groups.items()
        if len({(item.fact.value_raw, item.fact.unit_raw) for item in values}) == 1
    }
