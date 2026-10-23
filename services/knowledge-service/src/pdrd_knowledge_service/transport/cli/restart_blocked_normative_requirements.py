# services/knowledge-service/src/pdrd_knowledge_service/transport/cli/restart_blocked_normative_requirements.py

"""Одноразовый адресный CLI для неисчерпанного failed с retryable=false."""

import argparse
import asyncio
import json
from functools import partial
from pathlib import Path
from uuid import UUID

from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    NormativeRequirementCatalog,
)
from pdrd_knowledge_service.application.use_cases.restart_blocked_normative_requirements import (
    RestartBlockedNormativeRequirements,
)
from pdrd_knowledge_service.core.settings import get_settings
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from pdrd_knowledge_service.transport.cli.normative_requirements import save_report


def parser() -> argparse.ArgumentParser:
    """Требует точный документ и UUID неисправного поколения, а не область каталога."""
    command = argparse.ArgumentParser(
        description="Ручной адресный повтор заблокированного failed-поколения."
    )
    command.add_argument("--document-id", required=True, type=UUID)
    command.add_argument("--expected-generation-id", required=True, type=UUID)
    command.add_argument("--report", required=True, type=Path)
    return command


async def execute(args: argparse.Namespace) -> dict:
    """Собирает существующие PostgreSQL-зависимости без изменения retry policy."""
    settings = get_settings()
    engine = build_async_engine(settings.database)
    factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork, build_session_factory(engine)
    )
    try:
        generation = await RestartBlockedNormativeRequirements(
            NormativeRequirementCatalog(
                factory, policy=settings.normative_requirements.policy()
            )
        ).execute(
            document_id=args.document_id,
            expected_generation_id=args.expected_generation_id,
        )
        report = {
            "schema": "normative-requirements-manual-restart/v1",
            "document_id": str(args.document_id),
            "previous_generation_id": generation["previous_generation_id"],
            "generation_id": generation["generation_id"],
            "state": generation["state"],
            "administrative_restart_at": generation["administrative_restart_at"],
            "administrative_restart_reason": generation[
                "administrative_restart_reason"
            ],
        }
        save_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False))
        return report
    finally:
        await engine.dispose()


def main() -> None:
    """Не скрывает ошибку: повторный запуск после commit будет отклонён по UUID."""
    asyncio.run(execute(parser().parse_args()))


if __name__ == "__main__":
    main()
