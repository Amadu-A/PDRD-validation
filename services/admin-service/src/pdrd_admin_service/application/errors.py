# services/admin-service/src/pdrd_admin_service/application/errors.py

"""Ожидаемые отказы административных сценариев без раскрытия внутренней сети."""


class AuthenticationRequired(Exception):
    """Вход отсутствует или сессия больше не действительна."""


class PermissionDenied(Exception):
    """Действующая сессия не даёт административной операции."""


class CsrfRejected(Exception):
    """Заголовок защиты от межсайтового запроса отсутствует или неверен."""


class UpstreamUnavailable(Exception):
    """Доверенный внутренний сервис не ответил или нарушил контракт."""


class TargetNotFound(Exception):
    """Пользователь или назначение не существует."""


class RoleConflict(Exception):
    """Операция с ролью конфликтует с состоянием user-service."""


class InvalidRoleRequest(Exception):
    """User-service отверг область либо недопустимую смену роли."""
