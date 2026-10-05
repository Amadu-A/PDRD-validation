# services/user-service/alembic/versions/20261005_0002_local_accounts.py

"""Разрешает профиль локального аккаунта без email и AD."""

from alembic import op

revision = "20261005_0002"
down_revision = "20260930_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Обновляет допустимые виды профиля без удаления существующих данных."""
    op.drop_constraint("ck_accounts_kind", "accounts", schema="users")
    op.create_check_constraint(
        "ck_accounts_kind",
        "accounts",
        "kind IN ('corporate', 'external', 'local')",
        schema="users",
    )
    op.create_check_constraint(
        "ck_accounts_local_login",
        "accounts",
        "kind <> 'local' OR (login IS NOT NULL AND status <> 'pending_verification')",
        schema="users",
    )


def downgrade() -> None:
    """Отказывает в откате при локальных профилях, сохраняя их данные."""
    op.drop_constraint("ck_accounts_local_login", "accounts", schema="users")
    op.drop_constraint("ck_accounts_kind", "accounts", schema="users")
    op.create_check_constraint(
        "ck_accounts_kind",
        "accounts",
        "kind IN ('corporate', 'external')",
        schema="users",
    )
