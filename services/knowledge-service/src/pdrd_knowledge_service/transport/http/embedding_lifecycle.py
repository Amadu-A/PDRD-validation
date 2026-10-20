# services/knowledge-service/src/pdrd_knowledge_service/transport/http/embedding_lifecycle.py

"""Корреляция запросов n8n с lifecycle и компактными метриками embedding."""

import json
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from pdrd_knowledge_service.application.embedding_execution import (
    EmbeddingCancelledError,
    EmbeddingExecution,
    embedding_execution,
)
from pdrd_knowledge_service.application.ports.embedding import EmbeddingTemporaryError

logger = logging.getLogger(__name__)


class EmbeddingLifecycleMiddleware(BaseHTTPMiddleware):
    """Сохраняет прежние ответы API без служебного заголовка document ID."""

    def __init__(
        self, app, *, check_control: Callable[[UUID], Awaitable[dict]]
    ) -> None:
        """Подключает контроль только для запросов оркестратора с UUID документа."""
        super().__init__(app)
        self._check_control = check_control

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Не выдаёт пустые источники при перегрузке и отмечает ожидаемую отмену отдельно."""
        document = request.headers.get("X-PDRD-Document-ID")
        if not document:
            return await call_next(request)
        try:
            document_id = UUID(document)
        except ValueError:
            return JSONResponse(
                {"detail": {"code": "invalid_analysis_document_id"}}, status_code=422
            )
        stage = request.headers.get("X-PDRD-Stage", "retrieval")
        allowed = {
            "requirements",
            "user_packages",
            "finding_norms",
            "project_context",
            "document_context",
            "experience",
        }
        context = EmbeddingExecution(
            "",
            stage if stage in allowed else "retrieval",
            check_control=partial(self._check_control, document_id),
            document_id=str(document_id),
        )
        with embedding_execution(context):
            try:
                # Контроль нужен и при полном попадании в кэш, до выдачи источников.
                await context.checkpoint()
                response = await call_next(request)
                if "application/json" not in response.headers.get("content-type", ""):
                    return response
                body = b"".join([part async for part in response.body_iterator])
                payload = json.loads(body)
                if isinstance(payload, dict):
                    payload["retrieval_metrics"] = context.as_dict()
                    payload["retrieval_complete"] = response.status_code < 400
                headers = dict(response.headers)
                headers.pop("content-length", None)
                return JSONResponse(
                    payload, status_code=response.status_code, headers=headers
                )
            except EmbeddingCancelledError:
                return JSONResponse(
                    {
                        "status": "cancelled",
                        "cancelled": True,
                        "retrieval_complete": False,
                        "sources": [],
                        "normative_sources": [],
                        "technical_assignment_sources": [],
                        "retrieval_metrics": context.as_dict(),
                    }
                )
            except EmbeddingTemporaryError as error:
                return JSONResponse(
                    {
                        "detail": {
                            "code": error.reason_code,
                            "message": str(error),
                            "retry_managed": True,
                        },
                        "retrieval_complete": False,
                        "retrieval_metrics": context.as_dict(),
                    },
                    status_code=503,
                )
            finally:
                logger.info(
                    "embedding_retrieval metrics=%s",
                    json.dumps(context.as_dict()),
                    extra={"embedding_metrics": context.as_dict()},
                )
