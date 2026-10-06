# services/api-gateway/src/pdrd_api_gateway/application/experience_authors.py

"""Обогащает авторов каталога профилями User Service, сохраняя неизменяемый source."""

from uuid import UUID

from pdrd_api_gateway.application.ports.experience import ExperienceAuthorProfiles
from pdrd_api_gateway.application.ports.review import ReviewRequestError


def author_user_id(actor: str) -> UUID | None:
    """Исторический оператор не становится пользователем по похожему имени."""
    if not isinstance(actor, str) or not actor.startswith("user:"):
        return None
    try:
        return UUID(actor[5:])
    except ValueError:
        return None


async def enrich_authors(
    payload: dict, *, operation: str, actor: str, profiles: ExperienceAuthorProfiles
) -> dict:
    """Делает один batch-read для страницы; профиль не записывается в снимок замечания."""
    actor_id = author_user_id(actor)
    if actor_id is None:
        return payload
    if operation not in {"list", "authors", "read", "curate", "deactivate"}:
        return payload
    try:
        rows = payload["items"] if operation in {"list", "authors"} else [payload]
        references = [
            row["id"] if operation == "authors" else row["source"]["created_by"]
            for row in rows
        ]
        if not all(isinstance(value, str) for value in references):
            raise ValueError("Некорректный автор")
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewRequestError(503, "Каталог вернул некорректных авторов.") from error
    identities = tuple(
        dict.fromkeys(
            user_id
            for value in references
            if (user_id := author_user_id(value)) is not None
        )
    )
    unavailable = False
    try:
        resolved = (
            await profiles.read(actor_user_id=actor_id, user_ids=identities)
            if identities
            else {}
        )
    except ReviewRequestError:
        # Каталог остаётся доступен: UUID не заменяется выдуманным именем.
        resolved, unavailable = {}, True
    projected = []
    for row, reference in zip(rows, references, strict=True):
        user_id = author_user_id(reference)
        profile = resolved.get(user_id)
        author = {
            "id": reference,
            "user_id": str(user_id) if user_id else None,
            "login": (profile["login"] or reference) if profile else reference,
            "display_name": profile["display_name"] if profile else "",
            "roles": profile["roles"] if profile else [],
            "resolved": profile is not None,
        }
        projected.append({**row, "author": author})
    if operation in {"list", "authors"}:
        return {**payload, "items": projected, "authors_unavailable": unavailable}
    return {**projected[0], "authors_unavailable": unavailable}
