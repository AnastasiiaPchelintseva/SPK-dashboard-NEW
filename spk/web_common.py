"""spk/web_common.py — общие помощники веба: адрес клиента, сессия, проверка роли и CSRF.

Вынесено из `spk/web.py`, чтобы `spk/api.py` не импортировал приложение целиком.
"""

from __future__ import annotations

import secrets

from fastapi import Request


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


class Denied(Exception):
    def __init__(self, text: str, status: int = 403):
        self.text = text
        self.status = status


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def sess_of(request: Request):
    return getattr(request.state, "sess", None)


def require(request: Request, need: str = "viewer") -> dict:
    """Сессия с ролью не ниже `need`; иначе на вход или экран «доступ не выдан»."""
    from spk.security import role_allows

    sess = sess_of(request)
    if sess is None:
        raise Redirect(f"/login?next={request.url.path}")
    if need and not role_allows(sess["role"], need):
        if not sess["role"]:
            raise Denied("Доступ не выдан — обратитесь к администратору.")
        raise Denied("Недостаточно прав для этого раздела.")
    return sess


def check_csrf(request: Request, csrf: str) -> dict:
    sess = sess_of(request)
    if sess is None or not secrets.compare_digest(csrf or "", sess["csrf_token"]):
        raise Denied("Форма устарела. Обновите страницу и повторите.")
    return sess
