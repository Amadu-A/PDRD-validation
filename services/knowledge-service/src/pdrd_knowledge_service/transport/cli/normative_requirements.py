# services/knowledge-service/src/pdrd_knowledge_service/transport/cli/normative_requirements.py

"""Аудит и возобновляемый backfill: python -m ...transport.cli.normative_requirements backfill."""

import argparse
import asyncio
import json
import logging
from functools import partial
from pathlib import Path
from time import monotonic
from uuid import UUID

from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    NormativeRequirementCatalog,
)
from pdrd_knowledge_service.application.use_cases.recover_normative_requirements import (
    RecoverNormativeRequirements,
)
from pdrd_knowledge_service.core.settings import get_settings
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)


def parser():
    """Область выбирается по UUID из БД, а не по зашитым названиям разделов."""
    command = argparse.ArgumentParser(
        description="Атомарный нормативный каталог без embedding, VLM и мутаций Qdrant."
    )
    command.add_argument("command", choices=["backfill"])
    scope = command.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all-sections", action="store_true")
    scope.add_argument("--section-id", type=UUID)
    scope.add_argument("--document-id", type=UUID)
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--status", action="store_true")
    command.add_argument("--only-missing", action="store_true")
    command.add_argument("--retry-failed", action="store_true")
    command.add_argument(
        "--restart-exhausted",
        action="store_true",
        help="Создать новое поколение для одного исчерпанного failed; историю не сбрасывать.",
    )
    command.add_argument("--max-in-flight", type=int, default=2)
    command.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="Число документов за итерацию очереди; не SQL batch worker.",
    )
    command.add_argument("--wait", action=argparse.BooleanOptionalAction, default=True)
    command.add_argument("--timeout-seconds", type=float, default=7200)
    command.add_argument("--poll-seconds", type=float, default=5)
    command.add_argument("--report", type=Path, required=True)
    return command


def save_report(path, report):
    """Промежуточный отчёт заменяется целиком; прерывание не повреждает предыдущий JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


async def run(args, recovery):
    """Ждёт терминальных состояний через БД; построение выполняет только штатный Celery worker."""
    if not 1 <= args.max_in_flight <= recovery.catalog.policy.max_in_flight:
        raise ValueError(
            "--max-in-flight превышает настроенный admission limit или меньше 1."
        )
    if (
        not 1 <= args.batch_size <= 200
        or args.poll_seconds <= 0
        or args.timeout_seconds <= 0
    ):
        raise ValueError("Неверные интервалы или размер batch.")
    scope = {"section_id": args.section_id, "document_id": args.document_id}
    if args.restart_exhausted and (args.document_id is None or args.only_missing):
        raise ValueError(
            "--restart-exhausted требует один --document-id без --only-missing."
        )
    started = monotonic()
    first = True
    restart = None
    if args.restart_exhausted and not (args.dry_run or args.status):
        generation = await recovery.catalog.queue(
            document_id=args.document_id,
            restart_exhausted=True,
            max_in_flight=args.max_in_flight,
        )
        restart = {
            key: generation[key]
            for key in (
                "generation_id",
                "previous_generation_id",
                "previous_attempt_count",
                "previous_dispatch_count",
                "administrative_restart_at",
            )
        }
    while True:
        report = await recovery.inspect(
            **scope, verify=first or args.dry_run or args.status
        )
        first = False
        if restart is not None:
            report["administrative_restart"] = restart
        report["mode"] = (
            "dry_run" if args.dry_run else "status" if args.status else "backfill"
        )
        report["elapsed_seconds"] = round(monotonic() - started, 2)
        if args.dry_run or args.status:
            report["finished"] = True
            save_report(args.report, report)
            return report
        eligible = [
            row
            for row in report["documents"]
            if row["eligible"]
            and (args.retry_failed or row["atomic_state"] != "failed")
        ]
        pending = any(
            row["atomic_state"] in {"queued", "building"}
            or (
                args.retry_failed
                and row["atomic_state"] == "failed"
                and row["retryable"]
                and not row["retry_exhausted"]
            )
            for row in report["documents"]
        )
        if not eligible and not pending:
            report["finished"] = True
            report["complete"] = all(
                row["atomic_state"] == "ready" and row["source_coverage"] == "complete"
                for row in report["documents"]
            )
            save_report(args.report, report)
            return report
        if monotonic() - started >= args.timeout_seconds:
            report.update(
                finished=False,
                reason="timeout; повтор команды продолжит по сохранённым поколениям",
            )
            save_report(args.report, report)
            return report
        result = await recovery.execute(
            **scope,
            limit=args.batch_size,
            max_in_flight=args.max_in_flight,
            retry_failed=args.retry_failed,
            inspection=report,
        )
        report = {**report, "last_iteration": result["outcomes"], "finished": False}
        save_report(args.report, report)
        if not args.wait:
            return report
        await asyncio.sleep(args.poll_seconds)


async def execute(args):
    """Собирает только PostgreSQL-зависимости; оригинальные файлы CLI не открывает."""
    settings = get_settings()
    engine = build_async_engine(settings.database)
    factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork, build_session_factory(engine)
    )
    try:
        report = await run(
            args,
            RecoverNormativeRequirements(
                NormativeRequirementCatalog(
                    factory, policy=settings.normative_requirements.policy()
                )
            ),
        )
        print(
            json.dumps(
                {
                    "report": str(args.report),
                    "states": report["states"],
                    "finished": report["finished"],
                    "in_flight": report["in_flight"],
                },
                ensure_ascii=False,
            )
        )
        return (0 if report.get("complete", True) else 1) if report["finished"] else 2
    finally:
        await engine.dispose()


def main():
    """Возвращает ненулевой код при незавершённом ожидании, а не ложный успех backfill."""
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(asyncio.run(execute(parser().parse_args())))


if __name__ == "__main__":
    main()
