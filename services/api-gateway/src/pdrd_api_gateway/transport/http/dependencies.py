# services/api-gateway/src/pdrd_api_gateway/transport/http/dependencies.py

"""FastAPI dependencies для получения composition container.

HTTP-слой использует объект, заранее собранный при создании приложения,
и не создаёт repositories, clients или другие concrete зависимости
непосредственно внутри endpoints.
"""

from fastapi import Request

from pdrd_api_gateway.core.container import ApplicationContainer


def get_container(
    request: Request,
) -> ApplicationContainer:
    """Возвращает container текущего FastAPI-приложения.

    Args:
        request: Текущий HTTP-запрос FastAPI.

    Returns:
        Container со всеми runtime-зависимостями сервиса.

    Raises:
        RuntimeError: Composition root не был установлен при старте приложения.
    """
    container = getattr(
        request.app.state,
        "container",
        None,
    )

    if not isinstance(
        container,
        ApplicationContainer,
    ):
        raise RuntimeError(
            "API Gateway application container is not configured.",
        )

    return container
