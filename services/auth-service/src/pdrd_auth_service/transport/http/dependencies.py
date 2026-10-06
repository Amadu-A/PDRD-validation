# services/auth-service/src/pdrd_auth_service/transport/http/dependencies.py

"""Достаёт runtime текущего HTTP-приложения без глобального состояния."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from pdrd_auth_service.core.runtime import AuthRuntime


def get_runtime(request: Request) -> AuthRuntime:
    """Закрывает публичные маршруты, пока auth-service не настроен."""
    runtime = request.app.state.runtime
    if runtime is None:
        raise HTTPException(503, "Сервис входа пока не настроен")
    return runtime


Runtime = Annotated[AuthRuntime, Depends(get_runtime)]
