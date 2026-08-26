"""Allow teams to be created before employees.

Revision ID: e2a6c8d91f04
Revises: c4b9a2176e3d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e2a6c8d91f04"
down_revision: Union[str, Sequence[str], None] = "c4b9a2176e3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "teams",
        "created_by_user_id",
        existing_type=sa.Uuid(),
        existing_nullable=False,
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "teams",
        "created_by_user_id",
        existing_type=sa.Uuid(),
        existing_nullable=True,
        nullable=False,
    )
