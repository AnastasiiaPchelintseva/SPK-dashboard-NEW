"""spk/migrate.py — схема базы через Alembic (`spk/alembic/versions`), применяется при старте.

    python -m spk.migrate        # применить и показать текущую ревизию
"""

from __future__ import annotations

from pathlib import Path

from spk.database import CONNECT_ERRORS, DatabaseUnavailable, describe_failure, engine

ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"
#: Ключ замка миграций (не совпадает с замком пишущих транзакций).
MIGRATE_LOCK_KEY = 5_170_002


def _alembic_config():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    return cfg


def current_and_head() -> tuple[str | None, str]:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    with engine().connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    return current, head


def apply_migrations() -> str:
    """Применить непринятые ревизии. Нет базы — DatabaseUnavailable."""
    from alembic import command

    try:
        current, head = current_and_head()
    except CONNECT_ERRORS as exc:
        raise DatabaseUnavailable(describe_failure(exc)) from None
    if current == head:
        return current
    # Несколько процессов стартуют вместе: миграцию выполняет один, остальные ждут здесь.
    with engine().connect() as lock:
        lock.exec_driver_sql(f"SELECT pg_advisory_lock({MIGRATE_LOCK_KEY});")
        try:
            current, head = current_and_head()
            if current == head:
                return current
            command.upgrade(_alembic_config(), "head")
            return head
        finally:
            lock.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATE_LOCK_KEY});")


if __name__ == "__main__":
    print(f"ревизия схемы = {apply_migrations()}")
