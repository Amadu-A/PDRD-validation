# scripts/kb_sync.py

"""Синхронизация нормативной и опытной базы знаний с Qdrant.

Запуск:

    python -m scripts.kb_sync

Повторный запуск:

- неизменённые документы пропускаются;
- изменённый источник удаляется из Qdrant и индексируется заново;
- новые документы добавляются.
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


def normalize_text(text: str) -> str:
    """Нормализовать текст документа."""

    text = text.replace(
        "\x00",
        " ",
    )

    return re.sub(
        r"[ \t]+",
        " ",
        text,
    ).strip()


def file_sha256(path: Path) -> str:
    """Посчитать SHA-256."""

    digest = hashlib.sha256()

    with path.open("rb") as stream:
        while chunk := stream.read(
            1024 * 1024
        ):
            digest.update(chunk)

    return digest.hexdigest()


def chunk_text(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """Разрезать текст на перекрывающиеся части."""

    text = normalize_text(
        text
    )

    if not text:
        return []

    if overlap >= chunk_size:
        raise ValueError(
            "KB_CHUNK_OVERLAP должен быть "
            "меньше KB_CHUNK_SIZE."
        )

    chunks: list[str] = []

    start = 0

    while start < len(text):
        end = min(
            len(text),
            start + chunk_size,
        )

        chunk = text[
            start:end
        ].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - overlap

    return chunks


def batched(
    items: list[Any],
    batch_size: int,
) -> list[list[Any]]:
    """Разбить список на batches."""

    return [
        items[index:index + batch_size]
        for index in range(
            0,
            len(items),
            batch_size,
        )
    ]


def prepare_normative_chunks(
    pdf_path: Path,
    source_root: Path,
    chunk_size: int,
    overlap: int,
) -> list[dict[str, Any]]:
    """Извлечь текст нормативного PDF."""

    source_key = (
        "normative:"
        + pdf_path.relative_to(
            source_root
        ).as_posix()
    )

    source_sha256 = file_sha256(
        pdf_path
    )

    records: list[
        dict[str, Any]
    ] = []

    with fitz.open(
        pdf_path
    ) as document:
        for page_index, page in enumerate(
            document,
            start=1,
        ):
            text = page.get_text(
                "text",
                sort=True,
            )

            chunks = chunk_text(
                text,
                chunk_size,
                overlap,
            )

            for chunk_index, chunk in enumerate(
                chunks,
                start=1,
            ):
                records.append(
                    {
                        "source_key": source_key,
                        "source_sha256": source_sha256,
                        "source_file": pdf_path.name,
                        "source_path": (
                            pdf_path
                            .relative_to(
                                source_root
                            )
                            .as_posix()
                        ),
                        "page": page_index,
                        "chunk_index": chunk_index,
                        "text": chunk,
                    }
                )

    return records


def get_page_text(
    document: fitz.Document,
    page_number: int,
    limit: int = 6000,
) -> str:
    """Получить текст физической страницы PDF."""

    if (
        page_number < 1
        or page_number > len(document)
    ):
        return ""

    text = document[
        page_number - 1
    ].get_text(
        "text",
        sort=True,
    )

    return normalize_text(
        text
    )[:limit]


def prepare_experience_records(
    case_dir: Path,
) -> tuple[
    str,
    str,
    list[dict[str, Any]],
]:
    """Подготовить один подтверждённый проект."""

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
            "нет issues.json/meta.json"
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
            "issues": issues_data,
            "before_sha256": (
                meta_data["before"][
                    "sha256"
                ]
            ),
            "after_sha256": (
                meta_data["after"][
                    "sha256"
                ]
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")

    source_sha256 = hashlib.sha256(
        digest_source
    ).hexdigest()

    before_pdf = (
        case_dir
        / issues_data["before_pdf"]
    )

    after_pdf = (
        case_dir
        / issues_data["after_pdf"]
    )

    records: list[
        dict[str, Any]
    ] = []

    with (
        fitz.open(
            before_pdf
        ) as before_document,
        fitz.open(
            after_pdf
        ) as after_document,
    ):
        for issue in issues_data[
            "issues"
        ]:
            before_page = (
                issue["before"][
                    "pdf_page"
                ]
            )

            after_page = (
                issue["after"][
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

            issue_text = issue[
                "text"
            ]

            embedding_text = (
                f"Экспертное замечание: "
                f"{issue_text}\n"
                f"Проект: {project_id}\n"
                f"Страница до исправления: "
                f"{before_page}\n"
                f"Контекст листа до исправления:\n"
                f"{before_context}\n\n"
                f"Страница после исправления: "
                f"{after_page}\n"
                f"Контекст исправленного листа:\n"
                f"{after_context}"
            )

            records.append(
                {
                    "source_key": source_key,
                    "source_sha256": source_sha256,
                    "project_id": project_id,
                    "issue_id": issue[
                        "id"
                    ],
                    "issue_text": issue_text,
                    "before_page": before_page,
                    "after_page": after_page,
                    "bbox_points": (
                        issue["before"].get(
                            "bbox_points"
                        )
                    ),
                    "before_pdf": issues_data[
                        "before_pdf"
                    ],
                    "after_pdf": issues_data[
                        "after_pdf"
                    ],
                    "text": embedding_text,
                }
            )

    return (
        source_key,
        source_sha256,
        records,
    )


def index_records(
    *,
    records: list[dict[str, Any]],
    collection: str,
    source_key: str,
    source_sha256: str,
    embedding_client: OllamaEmbeddingClient,
    qdrant: QdrantRestClient,
    batch_size: int,
) -> int:
    """Проиндексировать один источник."""

    existing = qdrant.get_source_payload(
        collection,
        source_key,
    )

    if (
        existing
        and existing.get(
            "source_sha256"
        )
        == source_sha256
    ):
        print(
            "  [SKIP] Не изменён."
        )

        return 0

    if existing:
        print(
            "  [UPDATE] Удаляем старую версию."
        )

        qdrant.delete_source(
            collection,
            source_key,
        )

    if not records:
        print(
            "  [WARN] Нет текста для индексации."
        )

        return 0

    inserted = 0

    for batch in batched(
        records,
        batch_size,
    ):
        texts = [
            record["text"]
            for record in batch
        ]

        vectors = embedding_client.embed(
            texts
        )

        points = []

        for record, vector in zip(
            batch,
            vectors,
            strict=True,
        ):
            identity = (
                record.get(
                    "issue_id"
                )
                or (
                    f"{record.get('page')}:"
                    f"{record.get('chunk_index')}"
                )
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
            points
        )

    return inserted


def main() -> int:
    """Синхронизировать обе базы знаний."""

    repo_root = get_repo_root()
    settings = get_settings()

    normative_dir = (
        repo_root
        / "data"
        / "knowledge"
        / "normative"
        / "source"
    )

    cases_dir = (
        repo_root
        / "data"
        / "knowledge"
        / "experience"
        / "cases"
    )

    embedding_client = (
        OllamaEmbeddingClient(
            settings.ollama_url,
            settings.embedding_model,
        )
    )

    qdrant = QdrantRestClient(
        settings.qdrant_url
    )

    print(
        f"Ollama: {settings.ollama_url}"
    )

    print(
        f"Embedding model: "
        f"{settings.embedding_model}"
    )

    print(
        f"Qdrant: {settings.qdrant_url}"
    )

    if not qdrant.is_alive():
        print(
            "[ERROR] Qdrant недоступен."
        )
        return 1

    print()
    print(
        "Определяем размер embedding..."
    )

    probe_vector = embedding_client.embed(
        [
            "Проверка базы знаний"
        ]
    )[0]

    vector_size = len(
        probe_vector
    )

    print(
        f"Размер вектора: {vector_size}"
    )

    qdrant.ensure_collection(
        settings.normative_collection,
        vector_size,
    )

    qdrant.ensure_collection(
        settings.experience_collection,
        vector_size,
    )

    print()
    print(
        "=== НОРМАТИВНАЯ БАЗА ==="
    )

    normative_files = (
        sorted(
            normative_dir.glob(
                "*.pdf"
            )
        )
        if normative_dir.is_dir()
        else []
    )

    print(
        f"Найдено PDF: "
        f"{len(normative_files)}"
    )

    normative_points = 0

    for pdf_path in normative_files:
        print(
            f"\n{pdf_path.name}"
        )

        source_key = (
            "normative:"
            + pdf_path.relative_to(
                normative_dir
            ).as_posix()
        )

        source_sha256 = file_sha256(
            pdf_path
        )

        records = (
            prepare_normative_chunks(
                pdf_path,
                normative_dir,
                settings.chunk_size,
                settings.chunk_overlap,
            )
        )

        inserted = index_records(
            records=records,
            collection=(
                settings.normative_collection
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

        normative_points += inserted

        if inserted:
            print(
                f"  [OK] Добавлено chunks: "
                f"{inserted}"
            )

    print()
    print(
        "=== БАЗА ОПЫТА ==="
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
        f"Найдено проектов: "
        f"{len(case_dirs)}"
    )

    experience_points = 0

    for case_dir in case_dirs:
        print(
            f"\n{case_dir.name}"
        )

        try:
            (
                source_key,
                source_sha256,
                records,
            ) = prepare_experience_records(
                case_dir
            )

        except RuntimeError as error:
            print(
                f"  [SKIP] {error}"
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
                f"  [OK] Добавлено замечаний: "
                f"{inserted}"
            )

    print()
    print("=" * 72)

    print(
        "Синхронизация завершена."
    )

    print(
        f"Новых/обновлённых "
        f"нормативных chunks: "
        f"{normative_points}"
    )

    print(
        f"Новых/обновлённых "
        f"примеров опыта: "
        f"{experience_points}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )