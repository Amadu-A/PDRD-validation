# services/analysis-service/src/pdrd_analysis_service/application/decision_protocol.py

"""Связь внутреннего реестра с существующим происхождением замечания."""

from dataclasses import asdict, replace

from pdrd_analysis_service.domain.analysis import FindingDraft
from pdrd_analysis_service.domain.design_decisions import DecisionRegistry
from pdrd_analysis_service.domain.engineering_assertion import read_assertion


def attach_decision_protocols(
    findings: tuple[FindingDraft, ...], registry: DecisionRegistry, *, checks=()
) -> tuple[FindingDraft, ...]:
    """Присоединяет только точно связанные факты, не меняя статусы, текст и геометрию."""
    by_fact = {
        observation.evidence_id: decision
        for decision in registry.decisions
        for observation in decision.observations
    }
    evidence = {item.evidence_id: item for item in registry.evidence}
    result = []
    for finding in findings:
        assertion = read_assertion(finding.origin_assertions)
        if assertion is None:
            result.append(finding)
            continue
        linked = {}
        for fact in assertion.observations:
            decision = by_fact.get(fact.fact_id)
            # ID недостаточно: проекция старого/другого документа не должна связаться.
            if decision is not None and evidence[fact.fact_id].observation == fact:
                linked[decision.decision_id] = decision
        if not linked:
            result.append(finding)
            continue
        protocols = [decision.as_protocol() for decision in linked.values()]
        for protocol in protocols:
            evidence_ids = set(protocol["evidence_ids"])
            relevant = [
                check.as_dict()
                for check in checks
                if evidence_ids & {item.fact_id for item in check.observations}
            ]
            if relevant:
                protocol["checks"] = relevant
        related_ids = {
            identity
            for decision in linked.values()
            for identity in (
                *decision.purpose_evidence_ids,
                *decision.design_input_ids,
                *decision.calculation_ref_ids,
                *decision.requirement_ref_ids,
            )
        }
        origin = {
            "origin": "design_decision_registry",
            "version": 1,
            "document_id": registry.document_id,
            "decision_ids": list(linked),
            "decisions": protocols,
            "related_evidence": [asdict(evidence[key]) for key in sorted(related_ids)],
            "assessment_stage": "engineering_checks" if checks else "registry_only",
        }
        result.append(
            finding
            if origin in finding.origin_assertions
            else replace(
                finding, origin_assertions=(*finding.origin_assertions, origin)
            )
        )
    return tuple(result)
