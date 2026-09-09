# scripts/kb_sync.py

"""Синхронизация legacy Базы Опыта с Qdrant.

Нормативная база этим скриптом больше не управляется.

Нормативные документы добавляются только через managed catalog:

    Browser
        -> API Gateway
        -> Knowledge Service
        -> PostgreSQL / managed storage
        -> transactional outbox
        -> RabbitMQ
        -> knowledge-indexer
        -> Qdrant

Имя kb_sync.py временно сохранено для совместимости со старым
локальным workflow Базы Опыта.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import fitz

from scripts.kb_common import (
    OllamaEmbeddingClient,
    QdrantRestClient,
    get_repo_root,
    get_settings,
    stable_point_id,
)

EXPERIENCE_PAYLOAD_SCHEMA_VERSION = 2


def normalize_text(
    text: str,
) -> str:
    """Нормализует извлечённый текст."""
    text = text.replace(
        "\x00",
        " ",
    )

    return re.sub(
        r"[ \t]+",
        " ",
        text,
    ).strip()


def batched(
    items: list[Any],
    batch_size: int,
) -> list[
    list[Any]
]:
    """Разбивает список на batches."""
    return [
        items[
            index : index + batch_size
        ]
        for index in range(
            0,
            len(
                items,
            ),
            batch_size,
        )
    ]


def get_page_text(
    document: fitz.Document,
    page_number: int,
    limit: int = 6000,
) -> str:
    """Возвращает текст физической страницы PDF."""
    if (
        page_number < 1
        or page_number
        > len(
            document,
        )
    ):
        return ""

    text = document[
        page_number - 1
    ].get_text(
        "text",
        sort=True,
    )

    return normalize_text(
        text,
    )[:limit]


def prepare_experience_records(
    case_dir: Path,
) -> tuple[
    str,
    str,
    list[
        dict[
            str,
            Any,
        ]
    ],
]:
    """Подготавливает подтверждённые замечания проекта."""
    annotations_dir = (
        case_dir
        / "annotations"
    )

    issues_path = (
        annotations_dir
        / "issues.json"
    )

    meta_path = (
        annotations_dir
        / "meta.json"
    )

    if (
        not issues_path.is_file()
        or not meta_path.is_file()
    ):
        raise RuntimeError(
            "нет issues.json/meta.json",
        )

    issues_data = json.loads(
        issues_path.read_text(
            encoding="utf-8",
        )
    )

    meta_data = json.loads(
        meta_path.read_text(
            encoding="utf-8",
        )
    )

    project_id = issues_data[
        "project_id"
    ]

    source_key = (
        f"experience:{project_id}"
    )

    digest_source = json.dumps(
        {
            "payload_schema_version": (
                EXPERIENCE_PAYLOAD_SCHEMA_VERSION
            ),
            "issues": issues_data,
            "before_sha256": (
                meta_data[
                    "before"
                ][
                    "sha256"
                ]
            ),
            "after_sha256": (
                meta_data[
                    "after"
                ][
                    "sha256"
                ]
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode(
        "utf-8",
    )

    source_sha256 = hashlib.sha256(
        digest_source,
    ).hexdigest()

    before_pdf = (
        case_dir
        / issues_data[
            "before_pdf"
        ]
    )

    after_pdf = (
        case_dir
        / issues_data[
            "after_pdf"
        ]
    )

    records: list[
        dict[
            str,
            Any,
        ]
    ] = []

    with (
        fitz.open(
            before_pdf,
        ) as before_document,
        fitz.open(
            after_pdf,
        ) as after_document,
    ):
        for issue in issues_data[
            "issues"
        ]:
            before_page = (
                issue[
                    "before"
                ][
                    "pdf_page"
                ]
            )

            after_page = (
                issue[
                    "after"
                ][
                    "pdf_page"
                ]
            )

            before_context = get_page_text(
                before_document,
                before_page,
            )

            after_context = get_page_text(
                after_document,
                after_page,
            )

            issue_text = str(
                issue[
                    "text"
                ]
            )

            verified_fixed = bool(
                issue.get(
                    "verified_fixed",
                    False,
                )
            )

            embedding_text = (
                "Экспертное замечание: "
                f"{issue_text}\n"
                f"Проект: {project_id}\n"
                "Категория: "
                f"{issue.get('category') or ''}\n"
                "Страница до исправления: "
                f"{before_page}\n"
                "Контекст листа до исправления:\n"
                f"{before_context}\n\n"
                "Страница после исправления: "
                f"{after_page}\n"
                "Контекст исправленного листа:\n"
                f"{after_context}"
            )

            records.append(
                {
                    "source_key": (
                        source_key
                    ),
                    "source_sha256": (
                        source_sha256
                    ),
                    "payload_schema_version": (
                        EXPERIENCE_PAYLOAD_SCHEMA_VERSION
                    ),
                    "project_id": (
                        project_id
                    ),
                    "issue_id": (
                        issue[
                            "id"
                        ]
                    ),
                    "issue_text": (
                        issue_text
                    ),
                    "category": (
                        issue.get(
                            "category",
                        )
                    ),
                    "status": (
                        issue.get(
                            "status",
                        )
                    ),
                    "verified_fixed": (
                        verified_fixed
                    ),
                    "before_page": (
                        before_page
                    ),
                    "after_page": (
                        after_page
                    ),
                    "bbox_points": (
                        issue[
                            "before"
                        ].get(
                            "bbox_points",
                        )
                    ),
                    "before_pdf": (
                        issues_data[
                            "before_pdf"
                        ]
                    ),
                    "after_pdf": (
                        issues_data[
                            "after_pdf"
                        ]
                    ),
                    "before_context": (
                        before_context
                    ),
                    "after_context": (
                        after_context
                    ),
                    "text": (
                        embedding_text
                    ),
                }
            )

    return (
        source_key,
        source_sha256,
        records,
    )


def index_records(
    *,
    records: list[
        dict[
            str,
            Any,
        ]
    ],
    collection: str,
    source_key: str,
    source_sha256: str,
    embedding_client: OllamaEmbeddingClient,
    qdrant: QdrantRestClient,
    batch_size: int,
) -> int:
    """Индексирует один логический Experience source."""
    existing = qdrant.get_source_payload(
        collection,
        source_key,
    )

    if (
        existing
        and existing.get(
            "source_sha256",
        )
        == source_sha256
    ):
        print(
            "  [SKIP] Не изменён.",
        )

        return 0

    if existing:
        print(
            "  [UPDATE] Удаляем старую версию.",
        )

        qdrant.delete_source(
            collection,
            source_key,
        )

    if not records:
        print(
            "  [WARN] Нет данных для индексации.",
        )

        return 0

    inserted = 0

    for batch in batched(
        records,
        batch_size,
    ):
        texts = [
            record[
                "text"
            ]
            for record in batch
        ]

        vectors = embedding_client.embed(
            texts,
        )

        points = []

        for (
            record,
            vector,
        ) in zip(
            batch,
            vectors,
            strict=True,
        ):
            identity = (
                record.get(
                    "issue_id",
                )
                or "unknown"
            )

            points.append(
                {
                    "id": stable_point_id(
                        collection,
                        source_key,
                        identity,
                    ),
                    "vector": vector,
                    "payload": record,
                }
            )

        qdrant.upsert(
            collection,
            points,
        )

        inserted += len(
            points,
        )

    return inserted


def main() -> int:
    """Синхронизирует только legacy Базу Опыта."""
    repo_root = get_repo_root()

    settings = get_settings()

    cases_dir = (
        repo_root
        / "data"
        / "knowledge"
        / "experience"
        / "cases"
    )

    embedding_client = OllamaEmbeddingClient(
        settings.ollama_url,
        settings.embedding_model,
    )

    qdrant = QdrantRestClient(
        settings.qdrant_url,
    )

    print(
        f"Ollama: {settings.ollama_url}",
    )

    print(
        "Embedding model: "
        f"{settings.embedding_model}",
    )

    print(
        f"Qdrant: {settings.qdrant_url}",
    )

    print(
        "Нормативная база: "
        "managed catalog через UI/API. "
        "Этот скрипт её НЕ изменяет.",
    )

    if not qdrant.is_alive():
        print(
            "[ERROR] Qdrant недоступен.",
        )

        return 1

    print(
        "\nОпределяем размер embedding...",
    )

    probe_vector = embedding_client.embed(
        [
            "Проверка Базы Опыта",
        ]
    )[0]

    vector_size = len(
        probe_vector,
    )

    print(
        f"Размер вектора: {vector_size}",
    )

    qdrant.ensure_collection(
        settings.experience_collection,
        vector_size,
    )

    print(
        "\n=== БАЗА ОПЫТА ===",
    )

    case_dirs = (
        sorted(
            path
            for path in cases_dir.iterdir()
            if path.is_dir()
        )
        if cases_dir.is_dir()
        else []
    )

    print(
        "Найдено проектов: "
        f"{len(case_dirs)}",
    )

    experience_points = 0

    for case_dir in case_dirs:
        print(
            f"\n{case_dir.name}",
        )

        try:
            (
                source_key,
                source_sha256,
                records,
            ) = prepare_experience_records(
                case_dir,
            )

        except RuntimeError as error:
            print(
                f"  [SKIP] {error}",
            )

            continue

        inserted = index_records(
            records=records,
            collection=(
                settings.experience_collection
            ),
            source_key=source_key,
            source_sha256=source_sha256,
            embedding_client=(
                embedding_client
            ),
            qdrant=qdrant,
            batch_size=(
                settings.embed_batch_size
            ),
        )

        experience_points += inserted

        if inserted:
            print(
                "  [OK] Добавлено замечаний: "
                f"{inserted}",
            )

    print(
        "\n"
        + "=" * 78,
    )

    print(
        "Синхронизация Базы Опыта завершена.",
    )

    print(
        "Новых/обновлённых "
        "experience points: "
        f"{experience_points}",
    )

    print(
        "Experience collection: "
        f"{settings.experience_collection}",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )