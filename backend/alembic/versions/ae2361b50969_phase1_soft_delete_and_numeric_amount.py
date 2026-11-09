"""phase1_soft_delete_and_numeric_amount

Revision ID: ae2361b50969
Revises: 
Create Date: 2026-11-05 17:21:55.219185

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ae2361b50969"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: creates tables on fresh DB or migrates existing scans table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("full_name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    if "scans" not in existing_tables:
        op.create_table(
            "scans",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("document_type", sa.String(length=64), nullable=True),
            sa.Column("folio", sa.String(length=64), nullable=True),
            sa.Column("image_url", sa.String(length=512), nullable=False),
            sa.Column("thumbnail_url", sa.String(length=512), nullable=False),
            sa.Column("original_size_bytes", sa.Integer(), nullable=True),
            sa.Column("file_size_bytes", sa.Integer(), nullable=True),
            sa.Column("blur_score", sa.Float(), nullable=True),
            sa.Column("current_raw_text", sa.Text(), nullable=True),
            sa.Column("current_normalized_data", sa.JSON(), nullable=True),
            sa.Column("issue_date", sa.String(length=32), nullable=True),
            sa.Column(
                "total_amount",
                sa.Numeric(precision=14, scale=2),
                nullable=False,
                server_default="0.00",
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_scans_deleted_at"), "scans", ["deleted_at"], unique=False)
        op.create_index(op.f("ix_scans_document_type"), "scans", ["document_type"], unique=False)
        op.create_index(op.f("ix_scans_folio"), "scans", ["folio"], unique=False)
        op.create_index(op.f("ix_scans_issue_date"), "scans", ["issue_date"], unique=False)
        op.create_index(op.f("ix_scans_user_id"), "scans", ["user_id"], unique=False)
    else:
        existing_cols = {c["name"] for c in inspector.get_columns("scans")}
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("scans")}
        op.execute("UPDATE scans SET total_amount = 0.00 WHERE total_amount IS NULL")
        with op.batch_alter_table("scans", schema=None) as batch_op:
            if "deleted_at" not in existing_cols:
                batch_op.add_column(
                    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
                )
            batch_op.alter_column(
                "total_amount",
                existing_type=sa.FLOAT(),
                type_=sa.Numeric(precision=14, scale=2),
                nullable=False,
            )
            if "ix_scans_deleted_at" not in existing_indexes:
                batch_op.create_index(
                    batch_op.f("ix_scans_deleted_at"), ["deleted_at"], unique=False
                )

    if "scan_versions" not in existing_tables:
        op.create_table(
            "scan_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("scan_id", sa.String(length=36), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("raw_text", sa.Text(), nullable=True),
            sa.Column("normalized_data", sa.JSON(), nullable=True),
            sa.Column("modified_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_scan_versions_scan_id"), "scan_versions", ["scan_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "scans" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("scans")}
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("scans")}
        with op.batch_alter_table("scans", schema=None) as batch_op:
            if "ix_scans_deleted_at" in existing_indexes:
                batch_op.drop_index(batch_op.f("ix_scans_deleted_at"))
            batch_op.alter_column(
                "total_amount",
                existing_type=sa.Numeric(precision=14, scale=2),
                type_=sa.FLOAT(),
                nullable=True,
            )
            if "deleted_at" in existing_cols:
                batch_op.drop_column("deleted_at")
