"""Базовая схема: пользователи, сессии, журнал, настройки, документы дашборда.

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Мимо text(): двоеточия в комментариях не должны стать параметрами.
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("откат схемы не предусмотрен: восстановление — из копии базы")
