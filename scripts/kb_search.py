# scripts/kb_search.py

"""Ручная проверка semantic search legacy Базы Опыта.

Нормативный поиск этим скриптом больше не выполняется.

Managed normative retrieval должен проходить через Knowledge Service,
поскольку только он учитывает section_id, document_ids и состояние READY.
"""

from __future__ import annotations

import json

from scripts.kb_common import (
    OllamaEmbeddingClient,
    QdrantRestClient,
    get_repo_root,
    get_settings,
)


def load_experience_queries() -> list[
    dict
]:
    """Собирает подтверждённые замечания для меню."""
    cases_dir = (
        get_repo_root()
        / "data"
        / "knowledge"
        / "experience"
        / "cases"
    )

    result: list[
        dict
    ] = []

    if not cases_dir.is_dir():
        return result

    for case_dir in sorted(
        cases_dir.iterdir()
    ):
        issues_path = (
            case_dir
            / "annotations"
            / "issues.json"
        )

        if not issues_path.is_file():
            continue

        payload = json.loads(
            issues_path.read_text(
                encoding="utf-8",
            )
        )

        for issue in payload.get(
            "issues",
            [],
        ):
            result.append(
                {
                    "project_id": payload.get(
                        "project_id",
                    ),
                    "issue_id": issue.get(
                        "id",
                    ),
                    "text": issue.get(
                        "text",
                        "",
                    ),
                }
            )

    return result


def choose_query() -> str:
    """Выбирает запрос для поиска по Базе Опыта."""
    examples = load_experience_queries()

    if not examples:
        return input(
            "Введите поисковый запрос: ",
        ).strip()

    print(
        "Выберите подтверждённое "
        "экспертное замечание:",
    )

    for index, item in enumerate(
        examples,
        start=1,
    ):
        print(
            f"{index}. "
            f"[{item['project_id']}/"
            f"{item['issue_id']}] "
            f"{item['text']}"
        )

    while True:
        raw_value = input(
            "\nНомер: ",
        ).strip()

        try:
            number = int(
                raw_value,
            )

        except ValueError:
            print(
                "Введите номер.",
            )

            continue

        if (
            number < 1
            or number
            > len(
                examples,
            )
        ):
            print(
                "Такого номера нет.",
            )

            continue

        return examples[
            number - 1
        ][
            "text"
        ]


def print_experience_results(
    results: list[
        dict
    ],
) -> None:
    """Показывает похожие экспертные кейсы."""
    print()

    print(
        "=== БАЗА ОПЫТА ===",
    )

    if not results:
        print(
            "Ничего не найдено.",
        )

        return

    for index, item in enumerate(
        results,
        start=1,
    ):
        payload = item.get(
            "payload",
            {},
        )

        print()

        print(
            f"{index}. score="
            f"{item.get('score', 0):.4f}",
        )

        print(
            "   Проект: "
            f"{payload.get('project_id')}",
        )

        print(
            "   Issue: "
            f"{payload.get('issue_id')}",
        )

        print(
            "   Замечание: "
            f"{payload.get('issue_text')}",
        )

        print(
            "   BEFORE: "
            f"{payload.get('before_page')}"
            " -> AFTER: "
            f"{payload.get('after_page')}",
        )


def main() -> int:
    """Выполняет ручной semantic search Базы Опыта."""
    settings = get_settings()

    query = choose_query()

    if not query:
        print(
            "Пустой запрос.",
        )

        return 1

    print()

    print(
        f"Запрос: {query}",
    )

    print(
        "Нормативный поиск здесь отключён. "
        "Используйте основной managed analysis pipeline.",
    )

    embedding_client = OllamaEmbeddingClient(
        settings.ollama_url,
        settings.embedding_model,
    )

    qdrant = QdrantRestClient(
        settings.qdrant_url,
    )

    vector = embedding_client.embed(
        [
            query,
        ]
    )[0]

    experience = qdrant.query(
        settings.experience_collection,
        vector,
        limit=5,
    )

    print_experience_results(
        experience,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )