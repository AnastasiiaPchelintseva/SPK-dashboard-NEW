"""spk/database.py — подключение к PostgreSQL (PLATFORM.md, раздел 3).

SQL написан руками в репозиториях (`spk/repositories`). Этот слой даёт им простой интерфейс:
`conn.execute(sql, params)` с `?`-параметрами и строки с доступом по имени и по номеру.
Подключение — через SQLAlchemy (пул), схема — Alembic (`spk/migrate.py`).

Соединение в автокоммите; пишущая транзакция открывается `transaction(conn)`. Внутри неё
берётся общий замок `pg_advisory_xact_lock`: писатели идут по одному, порядковый номер правки
документа (`documents.seq`) растёт без пропусков и перестановок — на нём держится синхронизация
открытых страниц.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

from spk import config

#: Ключ общего замка пишущих транзакций (одно число на проект).
WRITE_LOCK_KEY = 5_170_001
CONNECT_TIMEOUT_S = 5

_engine = None
_engine_url = None


class DatabaseUnavailable(RuntimeError):
    """Базы нет или параметры неверны. Текст — без значений параметров."""


def _connect_errors() -> tuple:
    import psycopg
    from sqlalchemy.exc import OperationalError

    return (psycopg.OperationalError, OperationalError)


CONNECT_ERRORS = _connect_errors()


class Row(dict):
    """Строка результата: `row["col"]`, `row[0]`, `dict(row)`."""

    __slots__ = ("_values",)

    def __init__(self, names, values):
        super().__init__(zip(names, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, raw):
        self._raw = raw

    def _names(self):
        return [col.name for col in self._raw.description]

    def fetchone(self) -> Row | None:
        if self._raw.description is None:
            return None
        values = self._raw.fetchone()
        return None if values is None else Row(self._names(), values)

    def fetchall(self) -> list[Row]:
        if self._raw.description is None:
            return []
        names = self._names()
        return [Row(names, values) for values in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount


_QMARK = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\?")


def _translate(sql: str) -> str:
    """`?` → `%s` вне строковых литералов; `%` экранируется (параметры psycopg)."""
    sql = sql.replace("%", "%%")
    return _QMARK.sub(lambda m: "%s" if m.group(0) == "?" else m.group(0), sql)


class Connection:
    """Соединение поверх DBAPI-соединения psycopg из пула SQLAlchemy."""

    def __init__(self, pooled):
        self._pooled = pooled
        self._raw = pooled.driver_connection
        self._raw.autocommit = True
        self.in_transaction = False

    def execute(self, sql: str, params=()) -> Cursor:
        raw = self._raw.cursor()
        if params:
            raw.execute(_translate(sql), tuple(params))
        else:
            raw.execute(sql)
        return Cursor(raw)

    def close(self) -> None:
        if self._pooled is not None:
            if self.in_transaction:
                self._raw.rollback()
            self._pooled.close()
            self._pooled = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def database_url():
    from sqlalchemy.engine import URL

    return URL.create(
        "postgresql+psycopg",
        username=config.DB_USER,
        password=config.DB_PASSWORD or None,
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
    )


def engine():
    """Движок SQLAlchemy (пул соединений)."""
    global _engine, _engine_url
    from sqlalchemy import create_engine

    url = database_url()
    key = url.render_as_string(hide_password=False)
    if _engine is None or _engine_url != key:
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(
            url, pool_size=5, max_overflow=10, pool_pre_ping=True,
            connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
        )
        _engine_url = key
    return _engine


def describe_failure(exc: Exception) -> str:
    """Почему не подключились — по-человечески, без значений параметров."""
    text = str(exc).lower()
    if not config.DB_PASSWORD:
        return "нет пароля базы (DB_PASSWORD)"
    if "starting up" in text or "recovery mode" in text:
        return "база запускается, подключение повторится"
    if "password authentication failed" in text:
        return "неверный пароль или пользователь базы"
    if "does not exist" in text:
        return "база или пользователь не существует"
    if "timeout" in text or "connection refused" in text or "could not connect" in text \
            or "could not translate host name" in text or "name or service not known" in text:
        return "база недоступна (сервер PostgreSQL не отвечает)"
    return f"не удалось подключиться к базе ({type(exc).__name__})"


def connect() -> Connection:
    """Соединение из пула. Вызывающий закрывает сам (или через with)."""
    try:
        return Connection(engine().raw_connection())
    except CONNECT_ERRORS as exc:
        raise DatabaseUnavailable(describe_failure(exc)) from None


@contextmanager
def transaction(conn: Connection) -> Iterator[Connection]:
    """Пишущая транзакция: BEGIN + общий замок, commit/rollback."""
    conn.execute("BEGIN;")
    conn.in_transaction = True
    try:
        conn.execute("SELECT pg_advisory_xact_lock(%d);" % WRITE_LOCK_KEY)
        yield conn
    except Exception:
        conn.in_transaction = False
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.in_transaction = False
        conn.execute("COMMIT;")
