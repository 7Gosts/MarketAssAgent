"""Create journals table

Revision ID: journal_001
Revises:
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa

revision = "journal_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(128), index=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16)),
        sa.Column("entry_price", sa.Float),
        sa.Column("stop_loss", sa.Float),
        sa.Column("take_profit", sa.Float),
        sa.Column("status", sa.String(32), default="open"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("notes", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("journals")
