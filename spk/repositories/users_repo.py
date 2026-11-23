"""spk/repositories/users_repo.py — пользователи, серверные сессии и журнал аудита.

PLATFORM.md, разделы 4–10. Здесь только SQL; правила входа — `spk/security.py`.
В сессиях хранится хэш токена, а не сам токен.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from spk.database import Connection, Row


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_before(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="seconds")


# --- Пользователи ---------------------------------------------------------------

def get(conn: Connection, user_id: int) -> Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?;", (user_id,)).fetchone()


def by_yandex_id(conn: Connection, yandex_id: str) -> Row | None:
    return conn.execute("SELECT * FROM users WHERE yandex_id = ?;", (yandex_id,)).fetchone()


def by_username(conn: Connection, username: str) -> Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?;", (username,)).fetchone()


def local_admin(conn: Connection) -> Row | None:
    return conn.execute("SELECT * FROM users WHERE is_local_admin = 1;").fetchone()


def create(
    conn: Connection, *, username: str, email: str = "", display_name: str = "",
    role: str | None = None, status: str = "created", yandex_id: str | None = None,
    is_local_admin: bool = False, password_hash: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO users (yandex_id, username, email, display_name, role, status, "
        "is_local_admin, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id;",
        (yandex_id, username, email, display_name, role, status, int(is_local_admin),
         password_hash, utc_now()),
    )
    return cur.fetchone()["id"]


def update(conn: Connection, user_id: int, **fields) -> None:
    allowed = {"yandex_id", "username", "email", "display_name", "role", "status",
               "password_hash", "last_login_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"нельзя менять поля {sorted(unknown)}")
    if not fields:
        return
    names = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE users SET {names} WHERE id = ?;", (*fields.values(), user_id))


def delete(conn: Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ? AND is_local_admin = 0;", (user_id,))


def listing(conn: Connection, *, query: str = "", status: str = "") -> list[Row]:
    sql = "SELECT * FROM users WHERE 1 = 1"
    params: list = []
    if query:
        sql += " AND (username ILIKE ? OR email ILIKE ? OR display_name ILIKE ?)"
        params += [f"%{query}%"] * 3
    if status:
        sql += " AND status = ?"
        params.append(status)
    return conn.execute(sql + " ORDER BY is_local_admin DESC, username;", params).fetchall()


# --- Сессии ----------------------------------------------------------------------

def create_session(
    conn: Connection, *, user_id: int, token_hash: str, csrf_token: str,
    ip: str, user_agent: str,
) -> int:
    now = utc_now()
    cur = conn.execute(
        "INSERT INTO sessions (user_id, token_hash, csrf_token, created_at, "
        "last_activity_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id;",
        (user_id, token_hash, csrf_token, now, now, ip, user_agent[:400]),
    )
    return cur.fetchone()["id"]


def session_by_token_hash(conn: Connection, token_hash: str) -> Row | None:
    return conn.execute(
        "SELECT s.*, u.username, u.display_name, u.email, u.role, u.status, u.is_local_admin "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?;",
        (token_hash,),
    ).fetchone()


def touch_session(conn: Connection, session_id: int, *, ip: str) -> None:
    conn.execute(
        "UPDATE sessions SET last_activity_at = ?, ip = ? WHERE id = ?;",
        (utc_now(), ip, session_id),
    )


def delete_session(conn: Connection, session_id: int) -> Row | None:
    row = conn.execute(
        "SELECT s.id, s.user_id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.id = ?;", (session_id,),
    ).fetchone()
    conn.execute("DELETE FROM sessions WHERE id = ?;", (session_id,))
    return row


def delete_user_sessions(conn: Connection, user_id: int) -> int:
    return conn.execute("DELETE FROM sessions WHERE user_id = ?;", (user_id,)).rowcount


def delete_expired_sessions(conn: Connection, *, ttl_days: int) -> int:
    return conn.execute(
        "DELETE FROM sessions WHERE last_activity_at < ?;", (iso_before(days=ttl_days),)
    ).rowcount


def sessions_listing(conn: Connection, *, user_id: int | None = None) -> list[Row]:
    sql = (
        "SELECT s.id, s.user_id, s.created_at, s.last_activity_at, s.ip, s.user_agent, "
        "u.username FROM sessions s JOIN users u ON u.id = s.user_id"
    )
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE s.user_id = ?"
        params = (user_id,)
    return conn.execute(sql + " ORDER BY s.last_activity_at DESC;", params).fetchall()


# --- Журнал аудита ---------------------------------------------------------------

def _text(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def audit(
    conn: Connection, action: str, *, actor_user_id: int | None = None,
    actor: str = "", ip: str = "", target: str = "", old=None, new=None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor_user_id, actor, ip, action, target, old_value, "
        "new_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        (utc_now(), actor_user_id, actor, ip, action, target, _text(old), _text(new)),
    )


def count_actions_since(conn: Connection, action: str, *, ip: str, since: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = ? AND ip = ? AND at >= ?;",
        (action, ip, since),
    ).fetchone()[0]


def audit_listing(conn: Connection, *, action: str = "", limit: int = 300) -> list[Row]:
    if action:
        return conn.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?;", (action, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?;", (limit,)).fetchall()


def audit_actions(conn: Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY action;")]


# --- Ключи приложения --------------------------------------------------------------

def app_key(conn: Connection, name: str, make) -> str:
    """Ключ из базы; нет — создать `make()` и сохранить (первый старт)."""
    row = conn.execute("SELECT value FROM app_keys WHERE name = ?;", (name,)).fetchone()
    if row is not None:
        return row["value"]
    value = make()
    conn.execute(
        "INSERT INTO app_keys (name, value, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING;",
        (name, value, utc_now()),
    )
    return conn.execute("SELECT value FROM app_keys WHERE name = ?;", (name,)).fetchone()["value"]
