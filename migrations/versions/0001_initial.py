"""initial schema

Revision ID: 0001
Revises:
"""
from alembic import op

from database.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Baseline: creates every table/index/constraint defined in database/models.py.
    # For later schema changes run:  alembic revision --autogenerate -m "what changed"
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
