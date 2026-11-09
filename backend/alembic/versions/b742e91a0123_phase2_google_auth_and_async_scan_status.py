"""phase2_google_auth_and_async_scan_status

Revision ID: b742e91a0123
Revises: ae2361b50969
Create Date: 2026-11-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b742e91a0123"
down_revision: Union[str, Sequence[str], None] = "ae2361b50969"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_cols = {col["name"] for col in inspector.get_columns("users")}
    scan_cols = {col["name"] for col in inspector.get_columns("scans")}

    # 1. Modificaciones a la tabla users para Google OAuth
    if "google_id" not in user_cols:
        op.add_column(
            "users",
            sa.Column("google_id", sa.String(length=255), nullable=True),
        )
        op.create_index(op.f("ix_users_google_id"), "users", ["google_id"], unique=True)

    if "avatar_url" not in user_cols:
        op.add_column(
            "users",
            sa.Column("avatar_url", sa.String(length=512), nullable=True),
        )

    # En SQLite alter_column nullable requiere batch_alter_table
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("password_hash", existing_type=sa.String(length=255), nullable=True)

    # 2. Modificaciones a la tabla scans para cola asíncrona de Arq/Redis
    if "status" not in scan_cols:
        op.add_column(
            "scans",
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="COMPLETED",
            ),
        )
        op.create_index(op.f("ix_scans_status"), "scans", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_cols = {col["name"] for col in inspector.get_columns("users")}
    scan_cols = {col["name"] for col in inspector.get_columns("scans")}

    if "status" in scan_cols:
        op.drop_index(op.f("ix_scans_status"), table_name="scans")
        op.drop_column("scans", "status")

    if "google_id" in user_cols:
        op.drop_index(op.f("ix_users_google_id"), table_name="users")
        op.drop_column("users", "google_id")

    if "avatar_url" in user_cols:
        op.drop_column("users", "avatar_url")
