# services/api-gateway/src/pdrd_api_gateway/transport/http/request_actor.py

"""Передаёт прикладным операциям Review проверенного пользователя сессии.

Идентификатор создаёт только серверная проверка Auth Service. В закрытом
историческом режиме остаётся прежний серверный оператор.
"""

from fastapi import HTTPException, Request


def authenticated_actor(request: Request) -> str | None:
    """Возвращает auditable actor либо закрывает запрос без проверенной сессии."""
    if request.app.state.identity_authorizer is None:
        return None
    user_id = getattr(request.state, "identity_user_id", None)
    if user_id is None:
        raise HTTPException(401, "Требуется вход")
    return f"user:{user_id}"
