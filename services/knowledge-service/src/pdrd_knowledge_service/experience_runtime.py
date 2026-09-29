# services/knowledge-service/src/pdrd_knowledge_service/experience_runtime.py

"""CLI Experience: обработка ручных версий, проверочный поиск и оценка качества.

Watch берёт только вручную созданные задания; новый пример не запускает индексацию.
Preview/evaluate не меняют рабочий флаг E, shared-модели и n8n workflows.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import signal
import time
from argparse import Namespace
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

from pdrd_knowledge_service.core.experience import (
    build_experience_container,
    build_version_worker,
    prepare_version_search,
)
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.domain.experience_quality import (
    quality_report,
    validate_evaluation_cases,
)

logger = logging.getLogger(__name__)
STATUS = Path("/tmp/experience-index-status.json")


def write_json(path: Path, data: dict) -> None:
    """Заменяет собственный отчёт целиком, не оставляя частично записанный JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


async def run(arguments: Namespace, settings: Settings) -> None:
    """Выполняет выбранную команду с явно внедрённой конфигурацией."""
    if len(settings.experience.key.get_secret_value()) < 32:
        raise ValueError(
            "Индекс E требует отдельный служебный ключ длиной не менее 32 символов."
        )
    container = build_experience_container(settings, shadow=True)
    version = None
    if getattr(arguments, "version", None):
        container, version = await prepare_version_search(
            settings, container, arguments.version
        )
    search_options = {"section_id": version.section_id} if version else {}
    if arguments.command == "preview":
        results = await container.search.execute(arguments.query, **search_options)
        print(
            json.dumps(
                [
                    {
                        "query": result.query,
                        "sources": [
                            {
                                "source_id": source.source_id,
                                "example_id": source.example_id,
                                "tag": source.tag,
                                "learning_use": source.learning_use,
                                "score": source.score,
                                "context": source.before_context,
                            }
                            for source in result.sources
                        ],
                    }
                    for result in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if arguments.command == "evaluate":
        content = await asyncio.to_thread(arguments.dataset.read_bytes)
        dataset = json.loads(content)
        if (
            dataset.get("schema_version") != 1
            or dataset.get("split") != "held_out_documents"
        ):
            raise ValueError("Нужен версионированный набор held_out_documents.")
        cases, finding_cases = dataset["retrieval_cases"], dataset["finding_cases"]
        validate_evaluation_cases(cases, finding_cases)
        indexed_documents, cursor, seen = set(), None, set()
        indexed_hashes = set()
        while True:
            examples, following = await container.index.source.page(
                after=cursor, limit=100
            )
            indexed_documents.update(
                example.data["document_id"] for example in examples
            )
            indexed_hashes.update(example.data["source_sha256"] for example in examples)
            if following is None:
                break
            if following in seen or (cursor is not None and following <= cursor):
                raise ValueError("Источник E повторил либо уменьшил курсор оценки.")
            seen.add(following)
            cursor = following
        results = []
        for offset in range(0, len(cases), 20):
            results.extend(
                await container.search.execute(
                    [case["query"] for case in cases[offset : offset + 20]],
                    **search_options,
                )
            )
        report = quality_report(
            cases=cases,
            retrieved=[
                [source.example_id for source in result.sources] for result in results
            ],
            finding_cases=finding_cases,
            indexed_documents=indexed_documents,
            indexed_source_hashes=indexed_hashes,
        )
        report.update(
            dataset_sha256=hashlib.sha256(content).hexdigest(),
            embedding_identity=container.index.identity,
            collection=container.index.collection,
            evaluated_at=time.time(),
            top_k=container.search.top_k,
            min_score=container.search.min_score,
        )
        if version:
            report.update(
                version_id=str(version.id),
                manifest_sha256=version.manifest_sha256,
                section_id=version.section_id,
            )
        await asyncio.to_thread(
            write_json, arguments.output or settings.experience.quality_report, report
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if not settings.experience.index_enabled:
        raise ValueError("Синхронизация E выключена конфигурацией.")
    worker = build_version_worker(
        settings, container, worker=f"experience_{uuid4().hex}"
    )
    if arguments.command == "sync":
        result = await worker.execute()
        print(json.dumps(result, ensure_ascii=False))
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(name, stop.set)
    while not stop.is_set():
        try:
            result = await worker.execute()
            await asyncio.to_thread(
                write_json, STATUS, {**result, "last_success": time.time()}
            )
            if result.get("claimed"):
                logger.info(
                    "experience_version_complete %s",
                    json.dumps(result, ensure_ascii=False),
                )
        except Exception:
            # Отдельный цикл восстановления не завершает процесс после временной ошибки.
            logger.error("experience_version_failed: повтор после паузы")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), settings.experience.poll_seconds)


def main() -> None:
    """Парсит операторскую команду; служебный ключ не принимается из командной строки."""
    parser = argparse.ArgumentParser(description="Индекс подтверждённого Experience")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync")
    commands.add_parser("watch")
    preview = commands.add_parser("preview")
    preview.add_argument("--version", type=UUID, required=True)
    preview.add_argument("query", nargs="+")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--version", type=UUID, required=True)
    evaluate.add_argument("dataset", type=Path)
    evaluate.add_argument("--output", type=Path)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    asyncio.run(run(parser.parse_args(), Settings()))


if __name__ == "__main__":
    main()
