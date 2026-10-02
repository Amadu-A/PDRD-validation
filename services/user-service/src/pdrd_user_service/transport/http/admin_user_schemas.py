# services/user-service/src/pdrd_user_service/transport/http/admin_user_schemas.py

"""Ответ постраничного внутреннего списка пользователей для админки."""

from pdrd_user_service.application.use_cases.list_users import UserPage
from pdrd_user_service.transport.http.schemas import StrictSchema, UserResponse


class UserPageResponse(StrictSchema):
    """Передаёт профили без паролей и общее число для пагинации."""

    items: tuple[UserResponse, ...]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_domain(cls, page: UserPage) -> "UserPageResponse":
        """Отдаёт только разрешённые поля каждого профиля."""
        return cls(
            items=tuple(UserResponse.from_domain(user) for user in page.items),
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
