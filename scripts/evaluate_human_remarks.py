# scripts/evaluate_human_remarks.py

"""Проверяемый отчёт смысловой приёмки по матрице и явным решениям рецензента.

Скрипт не имитирует семантическую оценку поиском слов. Рецензент устанавливает
совпадение предмета, свойства и действия; код проверяет ссылки на реальные
результаты, страницы, цитаты и полноту оценки, сохраняя матрицу без изменений.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def load_matrix(payload) -> tuple[list[dict], int]:
    """Читает плоскую матрицу или пары sheet/comments, исключая только flag=false.

    Пустые включённые комментарии остаются в знаменателе: отсутствие сведений
    для смысловой оценки не означает разрешения исключить трудный пример.
    """
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        rows = []
        excluded = 0
        for item_index, item in enumerate(payload["items"], 1):
            sheet = item.get("sheet")
            if type(sheet) is not int or sheet <= 0:
                raise ValueError(
                    "sheet должен быть положительным физическим номером страницы PDF."
                )
            comments = item.get("comments")
            if not isinstance(comments, list):
                raise ValueError("Для каждого sheet необходим массив comments.")
            for comment_index, comment in enumerate(comments, 1):
                if not isinstance(comment, dict):
                    raise ValueError("Комментарий должен быть объектом.")
                if comment.get("flag") is False:
                    excluded += 1
                    continue
                rows.append(
                    {
                        **comment,
                        "hc_id": f"sheet-{sheet}:item-{item_index}:comment-{comment_index}",
                        "sheet": sheet,
                        "human_remark": str(comment.get("comment") or ""),
                        "original": comment,
                    }
                )
        return rows, excluded
    if isinstance(payload, dict):
        payload = next(
            (
                payload[key]
                for key in ("remarks", "human_remarks", "cases", "annotations")
                if isinstance(payload.get(key), list)
            ),
            None,
        )
    if not isinstance(payload, list):
        raise ValueError("Матрица должна содержать массив экспертных замечаний.")
    return [row for row in payload if row.get("flag") is not False], sum(
        row.get("flag") is False for row in payload
    )


def finding_pages(finding: dict) -> set[int]:
    """Отличает место претензии от страницы справочного контекста и проверки.

    У реквизита обложка может быть образцом правильной записи, но это не означает
    замечание к самой обложке. После объединения сохраняются все исходные места.
    """
    targets = {
        origin["check"]["observations"][0]["page"]
        for origin in finding.get("origin_assertions", [])
        if origin.get("check", {}).get("rule")
        in {"design_basis_dependency", "inventory_description"}
        and origin["check"].get("observations")
        and type(origin["check"]["observations"][0].get("page")) is int
    }
    if targets:
        return targets
    fields = set()
    for origin in finding.get("origin_assertions", []):
        for item in origin.get("observations", []):
            fact = item.get("fact", {})
            if (
                fact.get("property_type") == "document_quality"
                and fact.get("relation")
                in {
                    "document_part_code",
                    "document_part_role",
                    "section_code",
                    "project_title",
                    "instruction_scope",
                    "term_variant",
                }
                and type(item.get("page")) is int
            ):
                fields.add(item["page"])
    if fields:
        return fields
    return {
        int(finding["page"]),
        *(
            int(part["page"])
            for part in finding.get("evidence_locations", [])
            if part.get("page")
            and str(part.get("source_kind") or "D").upper() == "D"
            and not re.match(
                r"^(?:EQ|N|T|U|E)(?:\d+(?:$|[-:])|[-:])",
                str(part.get("source_id") or ""),
            )
        ),
    }


def evaluate(
    matrix: list[dict],
    findings: list[dict],
    decisions: list[dict],
    *,
    same_questions=(),
) -> dict:
    """Проверяет экспертные решения, не подменяя неизвестный результат положительным."""
    actual = {str(row["finding_id"]): row for row in findings}
    if len(actual) != len(findings):
        raise ValueError("Повтор finding_id в итоговом результате.")
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
        expected_sheet = case.get("sheet")
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
            if verdict in {"matched", "partial"} and finding.get("status") in {
                "hypothesis",
                "rejected",
                "not_evaluable",
            }:
                raise ValueError(
                    f"Диагностическая гипотеза не является воспроизведённым замечанием: {key}."
                )
            pages = finding_pages(finding)
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
            if expected_sheet is not None and expected_sheet not in selected:
                raise ValueError(
                    f"Замечание не воспроизведено на заданном листе {key}."
                )
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
        "reproduction": {
            "enabled_total": len(rows),
            "matched_percent": round(
                100 * sum(row["verdict"] == "matched" for row in rows) / len(rows), 2
            )
            if rows
            else None,
            "partial_count": sum(row["verdict"] == "partial" for row in rows),
        },
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
    matrix, excluded = load_matrix(
        json.loads(args.matrix.read_text(encoding="utf-8-sig"))
    )
    result = json.loads(args.result.read_text(encoding="utf-8-sig"))
    decisions = json.loads(args.decisions.read_text(encoding="utf-8-sig"))
    report = evaluate(
        matrix,
        result["findings"],
        decisions,
        same_questions=[group.split(",") for group in args.same_question],
    )
    report["excluded_flag_false"] = excluded
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
