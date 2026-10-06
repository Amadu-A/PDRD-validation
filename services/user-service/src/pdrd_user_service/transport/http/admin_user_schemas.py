# services/user-service/src/pdrd_user_service/transport/http/admin_user_schemas.py

"""Ответ постраничного внутреннего списка пользователей для админки."""

from dataclasses import asdict

from pdrd_user_service.application.use_cases.list_users import UserPage
from pdrd_user_service.transport.http.schemas import StrictSchema, UserResponse


class AdminUserResponse(UserResponse):
    """Добавляет к административному списку эффективное состояние ревью."""

    review_access: bool
    review_access_automatic: bool
    review_access_editable: bool


class UserPageResponse(StrictSchema):
    """Передаёт профили без паролей и общее число для пагинации."""

    items: tuple[AdminUserResponse, ...]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_domain(cls, page: UserPage) -> "UserPageResponse":
        """Отдаёт только разрешённые поля каждого профиля."""
        return cls(
            items=tuple(
                AdminUserResponse(
                    **UserResponse.from_domain(user).model_dump(), **asdict(state)
                )
                for user, state in zip(
                    page.items,
                    page.review_access_states,
                    strict=True,
                )
            ),
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
