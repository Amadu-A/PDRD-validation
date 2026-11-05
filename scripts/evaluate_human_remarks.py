# scripts/evaluate_human_remarks.py

"""Проверяемый отчёт смысловой приёмки по матрице и явным решениям рецензента.

Скрипт не имитирует семантическую оценку поиском слов. Рецензент устанавливает
совпадение предмета, свойства и действия; код проверяет ссылки на реальные
результаты, страницы, цитаты и полноту оценки, сохраняя матрицу без изменений.
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def evaluate(
    matrix: list[dict],
    findings: list[dict],
    decisions: list[dict],
    *,
    same_questions=(),
) -> dict:
    """Проверяет экспертные решения, не подменяя неизвестный результат положительным."""
    actual = {str(row["finding_id"]): row for row in findings}
    judged = {str(row["hc_id"]): row for row in decisions}
    if len(judged) != len(decisions):
        raise ValueError("Повтор HC-ID в решениях рецензента.")
    rows = []
    for case in matrix:
        key = str(case.get("hc_id") or case.get("id") or case.get("case_id") or "")
        if not key or key not in judged:
            raise ValueError(f"Нет явной смысловой оценки для {key!r}.")
        decision = judged[key]
        verdict = decision["verdict"]
        if (
            verdict not in {"matched", "partial", "not_matched"}
            or not str(decision.get("reason") or "").strip()
        ):
            raise ValueError(f"Некорректный вердикт/объяснение {key}.")
        evidence = []
        for match in decision.get("matches", []):
            finding = actual.get(str(match["finding_id"]))
            if finding is None:
                raise ValueError(f"Ссылка на отсутствующий finding: {key}.")
            pages = {int(finding["page"])}
            pages.update(
                int(part["page"])
                for part in finding.get("evidence_locations", [])
                if part.get("page")
            )
            pages.update(
                int(part["page"])
                for part in finding.get("document_context_basis_sources", [])
                if part.get("page")
            )
            selected = set(map(int, match.get("pages", [])))
            quote = str(match.get("quote") or "").strip()
            text = " ".join(
                str(finding.get(field) or "")
                for field in (
                    "comment",
                    "evidence",
                    "recommendation",
                    "recommendation_draft",
                )
            )
            if not selected or not selected <= pages or not quote or quote not in text:
                raise ValueError(f"Неподтверждённые страницы или цитата {key}.")
            if verdict == "matched" and any(
                match.get(key) is not True
                for key in ("object_match", "property_match", "action_match")
            ):
                raise ValueError(
                    f"Полное совпадение требует предмета, свойства и действия: {key}."
                )
            evidence.append({**match, "actual_pages": sorted(pages)})
        if verdict in {"matched", "partial"} and not evidence:
            raise ValueError(f"Положительная оценка без реальной находки: {key}.")
        rows.append(
            {
                "hc_id": key,
                "matrix_case": case,
                "verdict": verdict,
                "matches": evidence,
                "reason": decision["reason"],
            }
        )
    if set(judged) != {row["hc_id"] for row in rows}:
        raise ValueError("Решения содержат неизвестные матрице HC-ID.")
    if len({row["hc_id"] for row in rows}) != len(rows):
        raise ValueError("Повтор HC-ID в исходной матрице.")
    grouped_members = [key for group in same_questions for key in group]
    if len(set(grouped_members)) != len(grouped_members) or not set(
        grouped_members
    ) <= set(judged):
        raise ValueError("Группы вопросов содержат неизвестные или повторные HC-ID.")
    grouped = {
        key: index for index, group in enumerate(same_questions) for key in group
    }
    scores = {"not_matched": 0, "partial": 1, "matched": 2}
    unique = {}
    for row in rows:
        key = (
            ("group", grouped[row["hc_id"]])
            if row["hc_id"] in grouped
            else ("case", row["hc_id"])
        )
        unique[key] = max(unique.get(key, 0), scores[row["verdict"]])
    return {
        "schema": "pdrd.human-semantic-assessment.v1",
        "method": "explicit_reviewer_adjudication",
        "annotations": {
            "total": len(rows),
            **dict(Counter(row["verdict"] for row in rows)),
        },
        "unique_questions": {
            "total": len(unique),
            **dict(
                Counter(
                    {0: "not_matched", 1: "partial", 2: "matched"}[v]
                    for v in unique.values()
                )
            ),
        },
        "cases": rows,
    }


def main() -> None:
    """Читает исходные JSON без восстановления API-данных из PDF."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--same-question", action="append", default=[])
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8-sig"))
    result = json.loads(args.result.read_text(encoding="utf-8-sig"))
    decisions = json.loads(args.decisions.read_text(encoding="utf-8-sig"))
    if isinstance(matrix, dict):
        matrix = next(
            (
                matrix[key]
                for key in ("remarks", "human_remarks", "cases", "annotations")
                if isinstance(matrix.get(key), list)
            ),
            None,
        )
    if not isinstance(matrix, list):
        raise ValueError("Матрица должна содержать массив экспертных замечаний.")
    report = evaluate(
        matrix,
        result["findings"],
        decisions,
        same_questions=[group.split(",") for group in args.same_question],
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
