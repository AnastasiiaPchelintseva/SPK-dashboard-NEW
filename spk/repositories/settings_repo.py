"""spk/repositories/settings_repo.py — настройки (таблица settings).

Ключи и их описание — `spk/config.RUNTIME_SETTINGS`, умолчания — `RUNTIME_DEFAULTS`.
`apply_runtime` накладывает значения из базы на модуль config: правка в панели действует
без перезапуска. Секретов в проекте нет, но маска оставлена на будущее.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from spk import config
from spk.database import Connection, Row

SECRET_MASK = "••••••"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decode(raw: str):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _default(key: str):
    if key not in config.RUNTIME_SETTINGS:
        raise KeyError(f"неизвестная настройка {key!r}")
    return config.RUNTIME_DEFAULTS[key]


def is_secret(key: str) -> bool:
    meta = config.RUNTIME_SETTINGS.get(key)
    return meta is not None and meta.kind == "secret"


def masked(key: str, value):
    if is_secret(key):
        return SECRET_MASK if value else ""
    return value


def get(conn: Connection, key: str):
    """Значение настройки: из базы, иначе умолчание из config."""
    default = _default(key)
    row = conn.execute("SELECT value FROM settings WHERE key = ?;", (key,)).fetchone()
    if row is None:
        return default
    return _decode(row["value"])


def get_int(conn: Connection, key: str) -> int:
    return int(get(conn, key))


def coerce(key: str, value):
    """Привести и проверить значение по описанию. Неверное — ValueError."""
    meta = config.RUNTIME_SETTINGS[key]
    if meta.kind in ("int", "float"):
        if isinstance(value, bool) or value is None or str(value).strip() == "":
            raise ValueError(f"{key}: нужно число")
        try:
            number = int(value) if meta.kind == "int" else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key}: нужно число, получено {value!r}") from None
        if meta.bounds is not None:
            low, high = meta.bounds
            if not (low <= number <= high):
                raise ValueError(f"{key}={number} вне границ [{low}, {high}]")
        return number
    return "" if value is None else str(value).strip()


def set_value(conn: Connection, key: str, value, *, updated_by: str = "system") -> None:
    if key not in config.RUNTIME_SETTINGS:
        raise KeyError(f"неизвестная настройка {key!r}")
    value = coerce(key, value)
    conn.execute(
        "INSERT INTO settings (key, value, is_secret, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "is_secret = excluded.is_secret, updated_at = excluded.updated_at, "
        "updated_by = excluded.updated_by;",
        (key, json.dumps(value), int(is_secret(key)), _utc_now(), updated_by),
    )


def runtime_values(conn: Connection) -> dict:
    """Рабочие параметры: из базы, иначе умолчание."""
    result = dict(config.RUNTIME_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings;"):
        if row["key"] in result:
            result[row["key"]] = _decode(row["value"])
    return result


def apply_runtime(conn: Connection) -> None:
    """Наложить значения из базы на модуль config (неверное значение — остаётся умолчание)."""
    for row in conn.execute("SELECT key, value FROM settings;"):
        key = row["key"]
        if key not in config.RUNTIME_SETTINGS:
            continue
        try:
            value = coerce(key, _decode(row["value"]))
        except ValueError:
            continue
        setattr(config, key, value)


def set_value_audited(conn: Connection, key: str, value, *, author: str = "человек"):
    """Записать настройку с проверкой границ. Возвращает (было, стало); секреты — маской."""
    old = get(conn, key)
    set_value(conn, key, value, updated_by=author)
    new = get(conn, key)
    return masked(key, old), masked(key, new)


def changes(conn: Connection, limit: int = 50) -> list[Row]:
    """История правок настроек (из журнала аудита)."""
    return conn.execute(
        "SELECT at AS changed_at, target AS key, old_value, new_value, actor AS author "
        "FROM audit_log WHERE action = 'setting_changed' ORDER BY id DESC LIMIT ?;",
        (limit,),
    ).fetchall()
