"""spk/security.py — правила входа, сессий и ролей (PLATFORM.md, разделы 4–7, 9).

Без веб-фреймворка: функции принимают соединение с базой и возвращают итог, чтобы правила
проверялись тестами без HTTP. Сетевой вызов к Яндексу — в `spk/web.py`, сюда приходят уже
полученные данные пользователя.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from spk import config
from spk.database import Connection, Row
from spk.repositories import users_repo

log = logging.getLogger("spk.security")

ADMIN_USERNAME = "admin"
ADMIN_ROLE = "administrator"
ADMIN_MAX_FAILURES = 5
ADMIN_FAILURE_WINDOW_MIN = 15
TOUCH_EVERY_S = 60

_hasher = PasswordHasher()

#: Порядок ролей: старшая может всё, что младшая.
_RANK = {"viewer": 1, "editor": 2, "administrator": 3}


def role_allows(role: str | None, need: str) -> bool:
    return _RANK.get(role or "", 0) >= _RANK[need]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def username_from_email(email: str) -> str:
    return email.split("@", 1)[0].strip().lower()


def allowed_domains() -> set[str]:
    return {d.strip().lower() for d in (config.ALLOWED_EMAIL_DOMAINS or "").split(",") if d.strip()}


def domain_allowed(email: str) -> bool:
    if "@" not in email:
        return False
    return email.rsplit("@", 1)[1].strip().lower() in allowed_domains()


# --- Локальный администратор --------------------------------------------------------

def ensure_local_admin(conn: Connection) -> None:
    """Пользователь `admin` есть всегда; хэш сверяется с ADMIN_PASSWORD при старте.

    Пароль поменялся — новый хэш и все сессии admin удаляются. Пароля нет — вход
    администратора выключен (хэш стирается), в лог предупреждение.
    """
    row = users_repo.local_admin(conn)
    if row is None:
        user_id = users_repo.create(
            conn, username=ADMIN_USERNAME, display_name="Локальный администратор",
            role=ADMIN_ROLE, status="active", is_local_admin=True,
        )
        row = users_repo.get(conn, user_id)
        users_repo.audit(conn, "admin_created", actor="system", target=ADMIN_USERNAME)
    password = config.ADMIN_PASSWORD
    if not password:
        log.warning("ADMIN_PASSWORD не задан: вход администратора выключен")
        if row["password_hash"]:
            users_repo.update(conn, row["id"], password_hash=None)
            users_repo.delete_user_sessions(conn, row["id"])
        return
    if row["password_hash"] and _verify(row["password_hash"], password):
        return
    users_repo.update(conn, row["id"], password_hash=_hasher.hash(password))
    dropped = users_repo.delete_user_sessions(conn, row["id"])
    users_repo.audit(conn, "admin_password_changed", actor="system", target=ADMIN_USERNAME,
                     new={"sessions_dropped": dropped})


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def admin_login(conn: Connection, password: str, *, ip: str) -> tuple[bool, str]:
    """Проверить пароль admin. Не больше 5 неудач за 15 минут с одного IP."""
    since = users_repo.iso_before(minutes=ADMIN_FAILURE_WINDOW_MIN)
    failures = users_repo.count_actions_since(conn, "admin_login_failed", ip=ip, since=since)
    if failures >= ADMIN_MAX_FAILURES:
        users_repo.audit(conn, "admin_login_paused", actor=ADMIN_USERNAME, ip=ip)
        return False, f"Слишком много неудачных попыток. Повторите через {ADMIN_FAILURE_WINDOW_MIN} минут."
    row = users_repo.local_admin(conn)
    if row is None or not row["password_hash"]:
        return False, "Вход администратора выключен: не задан ADMIN_PASSWORD."
    if not password or not _verify(row["password_hash"], password):
        users_repo.audit(conn, "admin_login_failed", actor=ADMIN_USERNAME, ip=ip)
        return False, "Неверный пароль."
    users_repo.update(conn, row["id"], last_login_at=users_repo.utc_now())
    users_repo.audit(conn, "admin_login", actor_user_id=row["id"], actor=ADMIN_USERNAME, ip=ip)
    return True, ""


# --- Вход через Яндекс --------------------------------------------------------------

@dataclass
class YandexOutcome:
    user_id: int | None
    error: str = ""


CONTACT_ADMIN = "Вход невозможен — обратитесь к администратору."


def yandex_login(conn: Connection, info: dict, *, ip: str) -> YandexOutcome:
    """Данные `login.yandex.ru/info` → пользователь или отказ (PLATFORM.md, раздел 5).

    Проверить домен → найти по id, затем среди заранее созданных → состояние →
    обновить поля → (сессию создаёт вызывающий) → журнал.
    """
    yandex_id = str(info.get("id") or "").strip()
    email = str(info.get("default_email") or "").strip().lower()
    display = str(info.get("real_name") or info.get("display_name") or "").strip()
    if not yandex_id or not email:
        users_repo.audit(conn, "yandex_login_denied", ip=ip, target=email, new="нет id/email")
        return YandexOutcome(None, "Яндекс не передал id или email.")
    if not domain_allowed(email):
        users_repo.audit(conn, "yandex_login_denied", ip=ip, target=email, new="домен")
        return YandexOutcome(None, "Адрес не из разрешённого домена организации.")
    username = username_from_email(email)
    if username == ADMIN_USERNAME:
        users_repo.audit(conn, "yandex_login_denied", ip=ip, target=email, new="имя admin")
        return YandexOutcome(None, CONTACT_ADMIN)

    row = users_repo.by_yandex_id(conn, yandex_id)
    if row is None:
        row = users_repo.by_username(conn, username)
        if row is not None and row["yandex_id"] not in (None, ""):
            users_repo.audit(conn, "yandex_login_denied", ip=ip, target=username,
                             new="имя занято другим Яндекс id")
            return YandexOutcome(None, CONTACT_ADMIN)
    if row is not None and row["status"] == "blocked":
        users_repo.audit(conn, "yandex_login_denied", actor_user_id=row["id"], actor=row["username"],
                         ip=ip, target=row["username"], new="заблокирован")
        return YandexOutcome(None, "Доступ заблокирован.")

    now = users_repo.utc_now()
    if row is None:
        user_id = users_repo.create(
            conn, username=username, email=email, display_name=display, status="active",
            yandex_id=yandex_id,
        )
        users_repo.audit(conn, "user_registered", actor_user_id=user_id, actor=username, ip=ip,
                         target=username, new=email)
    else:
        user_id = row["id"]
        if row["username"] != username:
            clash = users_repo.by_username(conn, username)
            if clash is not None and clash["id"] != user_id:
                users_repo.audit(conn, "yandex_login_denied", ip=ip, target=username,
                                 new="новое имя занято")
                return YandexOutcome(None, CONTACT_ADMIN)
        users_repo.update(conn, user_id, yandex_id=yandex_id, username=username, email=email,
                          display_name=display or row["display_name"], status="active")
    users_repo.update(conn, user_id, last_login_at=now)
    users_repo.audit(conn, "yandex_login", actor_user_id=user_id, actor=username, ip=ip)
    return YandexOutcome(user_id)


# --- Сессии -------------------------------------------------------------------------

def start_session(conn: Connection, user_id: int, *, ip: str, user_agent: str) -> str:
    """Новая серверная сессия. Возвращает токен для cookie (в базе — его хэш)."""
    token = secrets.token_urlsafe(32)
    users_repo.create_session(
        conn, user_id=user_id, token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(32), ip=ip, user_agent=user_agent,
    )
    return token


def resolve_session(conn: Connection, token: str, *, ip: str) -> Row | None:
    """Сессия по токену из cookie: 7 дней с последней активности, касание раз в минуту."""
    if not token:
        return None
    row = users_repo.session_by_token_hash(conn, hash_token(token))
    if row is None:
        return None
    last = datetime.fromisoformat(row["last_activity_at"])
    now = datetime.now(timezone.utc)
    if now - last > timedelta(days=config.SESSION_TTL_DAYS) or row["status"] == "blocked":
        users_repo.delete_session(conn, row["id"])
        return None
    if (now - last).total_seconds() >= TOUCH_EVERY_S or row["ip"] != ip:
        users_repo.touch_session(conn, row["id"], ip=ip)
    return row
