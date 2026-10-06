# services/user-service/alembic/versions/20261005_0003_section_access.py

"""Создаёт множественные назначения разделов, их аудит и область роли sections."""

import sqlalchemy as sa
from alembic import op

revision = "20261005_0003"
down_revision = "20261005_0002"
branch_labels = None
depends_on = None


def _scope_constraints(include_sections: bool) -> None:
    """Заменяет ограничения существующих ролей без изменения назначений."""
    extra = ", 'sections'" if include_sections else ""
    for name in ("scope_kind", "scope", "head_scope"):
        op.drop_constraint(
            f"ck_role_assignments_{name}",
            "role_assignments",
            schema="users",
            type_="check",
        )
    op.create_check_constraint(
        "ck_role_assignments_scope_kind",
        "role_assignments",
        f"scope_kind IN ('platform', 'organization', 'department', 'own'{extra})",
        schema="users",
    )
    op.create_check_constraint(
        "ck_role_assignments_scope",
        "role_assignments",
        f"(scope_kind IN ('platform', 'own'{extra}) AND organization_id IS NULL "
        "AND department_id IS NULL) OR (scope_kind = 'organization' "
        "AND organization_id IS NOT NULL AND department_id IS NULL) OR "
        "(scope_kind = 'department' AND organization_id IS NOT NULL "
        "AND department_id IS NOT NULL)",
        schema="users",
    )
    op.create_check_constraint(
        "ck_role_assignments_head_scope",
        "role_assignments",
        f"role <> 'department_head' OR scope_kind IN ('department'{extra})",
        schema="users",
    )


def upgrade() -> None:
    """Добавляет назначения и аудит без FK к таблицам другого сервиса."""
    _scope_constraints(True)
    op.create_table(
        "section_access",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.accounts.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("section_id", sa.Uuid(), primary_key=True),
        schema="users",
    )
    op.create_table(
        "section_access_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.accounts.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.accounts.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("section_ids", sa.String(), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="users",
    )


def downgrade() -> None:
    """Удаляет таблицы после явного снятия назначений с новой областью."""
    _scope_constraints(False)
    op.drop_table("section_access_events", schema="users")
    op.drop_table("section_access", schema="users")
