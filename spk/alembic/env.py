"""Alembic: окружение миграций. Подключение — из spk.config (DB_*), см. spk/database.py."""

from __future__ import annotations

from alembic import context

from spk.database import engine


def run_migrations_online() -> None:
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("офлайн-режим Alembic не используется")
run_migrations_online()
