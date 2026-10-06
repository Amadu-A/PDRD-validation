# services/api-gateway/tests/unit/test_experience_authors.py

"""Авторы Experience обогащаются из User Service без подмены исторического источника."""

import json
from uuid import UUID

import httpx
import pytest
from pdrd_api_gateway.application.experience_authors import (
    author_user_id,
    enrich_authors,
)
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.infrastructure.experience_authors import HttpExperienceAuthors

ACTOR = UUID(int=1)
AUTHOR = UUID(int=2)


class Profiles:
    """Наблюдаемый источник актуального имени и роли автора."""

    def __init__(self, *, fail=False):
        """Сохраняет сценарий отказа и все batch-запросы."""
        self.fail, self.calls = fail, []

    async def read(self, **options):
        """Возвращает профиль либо безопасную недоступность сервиса."""
        self.calls.append(options)
        if self.fail:
            raise ReviewRequestError(503, "Профили недоступны")
        return {
            AUTHOR: {
                "login": "i.mein",
                "display_name": "Иван Мейн",
                "roles": ["department_head"],
            }
        }


async def test_batch_author_profiles_preserve_source_and_legacy_author():
    """Повторяющиеся авторы читаются одним запросом, legacy не привязывается к похожему логину."""
    profiles = Profiles()
    source = {"created_by": f"user:{AUTHOR}"}
    rows = [
        {"source": source},
        {"source": source},
        {"source": {"created_by": "engineer:server"}},
    ]
    result = await enrich_authors(
        {"items": rows}, operation="list", actor=f"user:{ACTOR}", profiles=profiles
    )
    assert profiles.calls == [{"actor_user_id": ACTOR, "user_ids": (AUTHOR,)}]
    assert result["items"][0]["source"] == source and "author" not in rows[0]
    assert result["items"][0]["author"]["roles"] == ["department_head"]
    assert result["items"][0]["author"]["display_name"] == "Иван Мейн"
    assert result["items"][2]["author"]["login"] == "engineer:server"
    assert not result["items"][2]["author"]["resolved"]


async def test_user_service_failure_does_not_remove_ready_catalog():
    """Сбой профилей оставляет UUID и явный статус без выдуманного имени."""
    payload = {"items": [{"source": {"created_by": f"user:{AUTHOR}"}}], "total": 1}
    result = await enrich_authors(
        payload, operation="list", actor=f"user:{ACTOR}", profiles=Profiles(fail=True)
    )
    assert result["total"] == 1 and result["authors_unavailable"]
    assert result["items"][0]["author"]["login"] == f"user:{AUTHOR}"
    assert result["items"][0]["author"]["display_name"] == ""


@pytest.mark.parametrize("value", ["i.mein", "engineer:server", "user:fake", ""])
def test_legacy_actor_is_not_mapped_to_user_uuid(value):
    """Разбор авторского UUID не предполагает существование одноимённого пользователя."""
    assert author_user_id(value) is None


async def test_http_profiles_use_server_actor_and_fixed_private_path():
    """Только Gateway формирует actor и Bearer, а ответ не может содержать посторонний профиль."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": str(AUTHOR),
                        "login": "i.mein",
                        "display_name": "Иван Мейн",
                        "roles": ["designer"],
                    }
                ]
            },
        )

    client = HttpExperienceAuthors(
        "http://user-service:8000",
        "service-key",
        transport=httpx.MockTransport(handler),
    )
    result = await client.read(actor_user_id=ACTOR, user_ids=(AUTHOR,))
    assert result[AUTHOR]["roles"] == ["designer"]
    assert calls[0].url.path == "/internal/v1/users/public-profiles"
    assert calls[0].headers["X-PDRD-Actor-Id"] == str(ACTOR)
    assert calls[0].headers["Authorization"] == "Bearer service-key"


async def test_http_profile_different_from_requested_uuid_is_rejected():
    """Профиль другого пользователя не подставляется в колонку автора."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": str(ACTOR),
                        "login": "other",
                        "display_name": "Другой",
                        "roles": [],
                    }
                ]
            },
        )

    client = HttpExperienceAuthors(
        "http://user-service:8000",
        "service-key",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ReviewRequestError) as error:
        await client.read(actor_user_id=ACTOR, user_ids=(AUTHOR,))
    assert error.value.status_code == 503


async def test_profiles_are_batched_and_refresh_current_role_without_shared_cache():
    """Ограничение User API соблюдается; роль после изменения не берётся из прежнего ответа."""
    batches = []
    roles = ["designer"]

    def handler(request):
        """Отвечает проекциями только запрошенных пользователей текущего запроса."""
        identities = json.loads(request.content)["user_ids"]
        batches.append(identities)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": identity,
                        "login": identity,
                        "display_name": "Пользователь",
                        "roles": roles,
                    }
                    for identity in identities
                ]
            },
        )

    client = HttpExperienceAuthors(
        "http://user-service:8000",
        "service-key",
        transport=httpx.MockTransport(handler),
    )
    identities = tuple(UUID(int=value) for value in range(1, 202))
    first = await client.read(actor_user_id=ACTOR, user_ids=identities)
    assert [len(batch) for batch in batches] == [100, 100, 1]
    assert len(first) == 201 and first[AUTHOR]["roles"] == ["designer"]
    roles = ["department_head"]
    current = await client.read(actor_user_id=ACTOR, user_ids=(AUTHOR,))
    assert current[AUTHOR]["roles"] == ["department_head"]
    assert first[AUTHOR]["roles"] == ["designer"]


async def test_historical_profile_without_login_keeps_name_and_role():
    """Отсутствующий логин старого профиля не делает остальные данные недоступными."""

    def handler(request):
        """Возвращает допустимый пустой логин внутреннего контракта User Service."""
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": str(AUTHOR),
                        "login": None,
                        "display_name": "Исторический автор",
                        "roles": ["designer"],
                    }
                ]
            },
        )

    client = HttpExperienceAuthors(
        "http://user:8000", "key", transport=httpx.MockTransport(handler)
    )
    result = await enrich_authors(
        {"items": [{"source": {"created_by": f"user:{AUTHOR}"}}]},
        operation="list",
        actor=f"user:{ACTOR}",
        profiles=client,
    )
    author = result["items"][0]["author"]
    assert author["resolved"] is True and result["authors_unavailable"] is False
    assert author["login"] == f"user:{AUTHOR}"
    assert author["display_name"] == "Исторический автор" and author["roles"] == [
        "designer"
    ]
