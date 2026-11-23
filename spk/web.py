"""spk/web.py — веб-интерфейс «Дашборда СПК» по PLATFORM.md (FastAPI + Jinja2).

Запуск из корня проекта:

    python -m uvicorn spk.web:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers

Вход — через Яндекс (Authlib, PKCE, `force_confirm=yes`) или локальный `admin`
(ADMIN_PASSWORD). Сессии серверные (таблица sessions), в cookie — случайный токен.
Все POST-формы — с токеном против подделки запросов (csrf сессии).

Страница дашборда (`dashboard.html`) отдаётся по `/` и ходит в API (`spk/api.py`).
"""

from __future__ import annotations

import logging
import secrets
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import itsdangerous  # noqa: E402
from authlib.integrations.starlette_client import OAuth, OAuthError  # noqa: E402
from fastapi import FastAPI, Form, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from spk import api, config  # noqa: E402
from spk.database import DatabaseUnavailable, connect, transaction  # noqa: E402
from spk.migrate import apply_migrations  # noqa: E402
from spk.repositories import documents_repo, settings_repo, users_repo  # noqa: E402
from spk.security import role_allows  # noqa: E402
from spk import security  # noqa: E402
from spk.web_common import Denied, Redirect, check_csrf, client_ip, require, sess_of  # noqa: E402

log = logging.getLogger("spk.web")

TEMPLATES = Jinja2Templates(directory=str(config.TEMPLATES_DIR))

#: Разделы меню: (адрес, название, нужная роль).
NAV = (
    ("/", "Дашборд", "viewer"),
    ("/settings", "Настройки", "administrator"),
    ("/admin/users", "Пользователи", "administrator"),
    ("/admin/sessions", "Сессии", "administrator"),
    ("/admin/audit", "Журнал аудита", "administrator"),
)


def _sess(request: Request):
    return sess_of(request)


def flash(request: Request, text: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", []).append([kind, text])


def back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def render(request: Request, template: str, **context) -> HTMLResponse:
    sess = _sess(request)
    role = sess["role"] if sess else None
    nav = [(url, title) for url, title, need in NAV if sess and role_allows(role, need)]
    context.update(
        request=request,
        sess=sess,
        nav=nav,
        path=request.url.path,
        flashes=request.session.pop("flash", []),
        can_edit=bool(sess and role_allows(role, "editor")),
        is_admin=bool(sess and role_allows(role, "administrator")),
        problems=config.settings_problems() if sess else [],
        roles=config.ROLES,
        title=config.APP_TITLE,
    )
    return TEMPLATES.TemplateResponse(request, template, context)


def _audit(request: Request, action: str, *, target: str = "", old=None, new=None) -> None:
    sess = _sess(request)
    conn = connect()
    try:
        with transaction(conn):
            users_repo.audit(
                conn, action, actor_user_id=sess["user_id"] if sess else None,
                actor=sess["username"] if sess else "", ip=client_ip(request),
                target=target, old=old, new=new,
            )
    finally:
        conn.close()


# --- Приложение ---------------------------------------------------------------------

def _startup() -> str:
    """Миграции, настройки, `admin`, уборка сессий. Возвращает ключ cookie состояния."""
    apply_migrations()
    conn = connect()
    try:
        with transaction(conn):
            settings_repo.apply_runtime(conn)
            security.ensure_local_admin(conn)
            users_repo.delete_expired_sessions(conn, ttl_days=config.SESSION_TTL_DAYS)
            return users_repo.app_key(conn, "oauth_state", lambda: secrets.token_urlsafe(48))
    finally:
        conn.close()


def _oauth() -> OAuth | None:
    if not (config.YANDEX_CLIENT_ID and config.YANDEX_CLIENT_SECRET):
        log.warning("YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET не заданы: вход через Яндекс выключен")
        return None
    oauth = OAuth()
    oauth.register(
        "yandex",
        client_id=config.YANDEX_CLIENT_ID,
        client_secret=config.YANDEX_CLIENT_SECRET,
        authorize_url="https://oauth.yandex.ru/authorize",
        access_token_url="https://oauth.yandex.ru/token",
        client_kwargs={"scope": "login:email login:info", "code_challenge_method": "S256"},
    )
    return oauth


class Readiness:
    """Готова ли база. Нет — режим «Не настроено» и повтор в фоне (PLATFORM.md, раздел 2)."""

    RETRY_S = 30

    def __init__(self):
        self.ready = False
        self.reason = ""
        # Ключ подписи cookie состояния OAuth. До подключения базы — временный, после — из базы.
        # Временный ключ НЕ выбрасывается: пока база не подключилась, cookie, выданные с ним,
        # должны оставаться валидными, иначе форма входа отклоняется как «устаревшая».
        self.state_key = secrets.token_urlsafe(48)
        self._lock = threading.Lock()

    def try_start(self) -> bool:
        with self._lock:
            if self.ready:
                return True
            try:
                key = _startup()
            except DatabaseUnavailable as exc:
                self.reason = str(exc)
                log.warning("база недоступна: %s; повтор через %s с", exc, self.RETRY_S)
                return False
            # Ключ из базы стабилен между перезапусками: cookie состояния переживают выкладку.
            self.state_key = key
            self.ready, self.reason = True, ""
            log.info("база подключена, миграции применены")
            return True

    def retry_in_background(self) -> None:
        def loop():
            while not self.try_start():
                time.sleep(self.RETRY_S)

        threading.Thread(target=loop, name="db-retry", daemon=True).start()


class _StateCookieMiddleware(SessionMiddleware):
    """SessionMiddleware, чей ключ подписи берётся из базы, когда она подключится.

    Ключ меняется только один раз — с временного на ключ из базы. Дальше он постоянный,
    поэтому cookie состояния (в них csrf-токен формы входа) не «протухают» при перезапуске.
    """

    def __init__(self, app, *, readiness: Readiness, **kwargs):
        super().__init__(app, secret_key=readiness.state_key, **kwargs)
        self._readiness = readiness
        self._key = readiness.state_key

    async def __call__(self, scope, receive, send):
        if self._key != self._readiness.state_key:
            self._key = self._readiness.state_key
            self.signer = itsdangerous.TimestampSigner(self._key)
        await super().__call__(scope, receive, send)


def not_configured(request: Request, reason: str) -> HTMLResponse:
    """Страница «Не настроено»: что не так и чего не хватает — без значений параметров."""
    missing = [name for name in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
               if not getattr(config, name)]
    response = TEMPLATES.TemplateResponse(
        request, "not_configured.html",
        {"request": request, "reason": reason, "missing": missing, "sess": None, "nav": [],
         "flashes": [], "problems": [], "retry_s": Readiness.RETRY_S, "title": config.APP_TITLE},
    )
    response.status_code = 503
    return response


def create_app() -> FastAPI:
    readiness = Readiness()
    if not readiness.try_start():
        readiness.retry_in_background()
    oauth = _oauth()
    app = FastAPI(title=config.APP_TITLE, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.readiness = readiness

    @app.middleware("http")
    async def session_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/static/"):
            return await call_next(request)
        if not readiness.ready:
            return not_configured(request, readiness.reason)
        token = request.cookies.get(config.SESSION_COOKIE_NAME, "")
        try:
            conn = connect()
        except DatabaseUnavailable as exc:
            return not_configured(request, str(exc))
        try:
            # Настройки из базы на каждый запрос: правка действует без перезапуска.
            settings_repo.apply_runtime(conn)
            sess = None
            if token:
                with transaction(conn):
                    row = security.resolve_session(conn, token, ip=client_ip(request))
                sess = dict(row) if row is not None else None
                if sess is not None:
                    sess["session_id"] = sess["id"]
        finally:
            conn.close()
        request.state.sess = sess
        response: Response = await call_next(request)
        if sess is not None and not getattr(request.state, "logged_out", False):
            _set_cookie(request, response, token)
        return response

    # Подпись cookie состояния OAuth (Authlib); ключ живёт в базе.
    app.add_middleware(_StateCookieMiddleware, readiness=readiness,
                       session_cookie=config.STATE_COOKIE_NAME, same_site="lax",
                       https_only=False, max_age=None)

    @app.exception_handler(DatabaseUnavailable)
    async def _on_db_down(request: Request, exc: DatabaseUnavailable):
        return not_configured(request, str(exc))

    @app.exception_handler(Redirect)
    async def _on_redirect(request: Request, exc: Redirect):
        return back(exc.url)

    @app.exception_handler(Denied)
    async def _on_denied(request: Request, exc: Denied):
        response = render(request, "message.html", title="Нет доступа", text=exc.text)
        response.status_code = exc.status
        return response

    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
    app.include_router(api.router)

    _routes_auth(app, oauth)
    _routes_dashboard(app)
    _routes_settings(app)
    _routes_admin(app)
    return app


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE_NAME, token, max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/",
    )


def _login_session(request: Request, user_id: int, target: str) -> RedirectResponse:
    conn = connect()
    try:
        with transaction(conn):
            token = security.start_session(
                conn, user_id, ip=client_ip(request),
                user_agent=request.headers.get("user-agent", ""),
            )
    finally:
        conn.close()
    response = back(target if target.startswith("/") and not target.startswith("//") else "/")
    _set_cookie(request, response, token)
    return response


# --- Вход и выход -------------------------------------------------------------------

def _routes_auth(app: FastAPI, oauth: OAuth | None) -> None:
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if _sess(request) is not None:
            return back("/")
        request.session["next"] = next
        return render(request, "login.html", yandex=oauth is not None,
                      admin_enabled=bool(config.ADMIN_PASSWORD))

    @app.get("/login/admin", response_class=HTMLResponse)
    def admin_login_page(request: Request):
        request.session["login_csrf"] = secrets.token_urlsafe(24)
        return render(request, "login_admin.html", login_csrf=request.session["login_csrf"],
                      admin_enabled=bool(config.ADMIN_PASSWORD))

    @app.post("/login/admin")
    def admin_login(request: Request, password: str = Form(""), csrf: str = Form("")):
        expected = request.session.pop("login_csrf", "")
        if not expected or not secrets.compare_digest(csrf, expected):
            flash(request, "Форма устарела, повторите вход.", "error")
            return back("/login/admin")
        conn = connect()
        try:
            with transaction(conn):
                ok, error = security.admin_login(conn, password, ip=client_ip(request))
                admin = users_repo.local_admin(conn)
        finally:
            conn.close()
        if not ok:
            flash(request, error, "error")
            return back("/login/admin")
        return _login_session(request, admin["id"], request.session.pop("next", "/"))

    @app.get("/login/yandex")
    async def yandex_start(request: Request):
        if oauth is None:
            raise Denied("Вход через Яндекс не настроен (YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET).")
        redirect_uri = str(request.url_for("yandex_callback"))
        # Яндекс всегда спрашивает аккаунт: иначе молча войдёт под последним (PLATFORM.md, 5).
        return await oauth.yandex.authorize_redirect(request, redirect_uri, force_confirm="yes")

    @app.get("/signin-yandex", name="yandex_callback")
    async def yandex_callback(request: Request):
        if oauth is None:
            raise Denied("Вход через Яндекс не настроен.")
        try:
            token = await oauth.yandex.authorize_access_token(request)
            resp = await oauth.yandex.get(
                "https://login.yandex.ru/info?format=json", token=token,
                headers={"Authorization": f"OAuth {token['access_token']}"},
            )
            info = resp.json()
        except (OAuthError, KeyError, ValueError) as exc:
            log.warning("вход через Яндекс не удался: %s", exc)
            flash(request, "Вход через Яндекс не удался, попробуйте ещё раз.", "error")
            return back("/login")
        return yandex_finish(request, info)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        sess = check_csrf(request, csrf)
        conn = connect()
        try:
            with transaction(conn):
                users_repo.delete_session(conn, sess["session_id"])
                users_repo.audit(conn, "logout", actor_user_id=sess["user_id"],
                                 actor=sess["username"], ip=client_ip(request))
        finally:
            conn.close()
        request.state.logged_out = True
        response = back("/login")
        response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")
        return response

    @app.get("/account/sessions", response_class=HTMLResponse)
    def my_sessions(request: Request):
        sess = require(request, "")
        conn = connect()
        try:
            rows = [dict(r) for r in users_repo.sessions_listing(conn, user_id=sess["user_id"])]
        finally:
            conn.close()
        return render(request, "sessions.html", rows=_local_times(rows), mine=True)

    @app.post("/account/sessions/{session_id}/delete")
    def my_session_delete(request: Request, session_id: int, csrf: str = Form("")):
        sess = check_csrf(request, csrf)
        _drop_session(request, session_id, only_user=sess["user_id"])
        return back("/account/sessions")


def yandex_finish(request: Request, info: dict) -> Response:
    """Данные Яндекса → правила входа → сессия. Отдельно от сети — для тестов."""
    conn = connect()
    try:
        with transaction(conn):
            outcome = security.yandex_login(conn, info, ip=client_ip(request))
    finally:
        conn.close()
    if outcome.user_id is None:
        return render(request, "message.html", title="Вход не выполнен", text=outcome.error)
    return _login_session(request, outcome.user_id, request.session.pop("next", "/"))


def _drop_session(request: Request, session_id: int, *, only_user: int | None = None) -> None:
    conn = connect()
    try:
        with transaction(conn):
            row = conn.execute("SELECT user_id FROM sessions WHERE id = ?;", (session_id,)).fetchone()
            if row is None or (only_user is not None and row["user_id"] != only_user):
                return
            dropped = users_repo.delete_session(conn, session_id)
            sess = _sess(request)
            users_repo.audit(conn, "session_deleted", actor_user_id=sess["user_id"],
                             actor=sess["username"], ip=client_ip(request),
                             target=dropped["username"] if dropped else str(session_id))
    finally:
        conn.close()


def _local_times(rows: list[dict], *fields: str) -> list[dict]:
    for row in rows:
        for field in fields or ("created_at", "last_activity_at", "last_login_at", "at"):
            if row.get(field):
                row[field] = to_local(row[field])
    return rows


def to_local(value: str) -> str:
    """UTC-время из базы → местное время (в базе всегда UTC)."""
    from datetime import datetime, timezone

    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


# --- Дашборд ------------------------------------------------------------------------

def _routes_dashboard(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        require(request)
        return FileResponse(config.DASHBOARD_FILE, media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/health/ready")
    def health_ready():
        conn = connect()
        try:
            conn.execute("SELECT 1;")
        finally:
            conn.close()
        return {"status": "ok"}


# --- Настройки ----------------------------------------------------------------------

def _routes_settings(app: FastAPI) -> None:
    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        require(request, "administrator")
        conn = connect()
        try:
            values = settings_repo.runtime_values(conn)
            stored = {row["key"] for row in conn.execute("SELECT key FROM settings;")}
            history = [dict(r) for r in settings_repo.changes(conn)]
            stats = documents_repo.stats(conn)
        finally:
            conn.close()
        rows = []
        for key, meta in config.RUNTIME_SETTINGS.items():
            value = values[key]
            rows.append({
                "key": key, "title": meta.title, "kind": meta.kind, "help": meta.help,
                "bounds": meta.bounds, "value": settings_repo.masked(key, value),
                "is_set": value not in ("", None), "stored": key in stored,
            })
        return render(request, "settings.html", rows=rows, history=_local_times(history),
                      stats=stats)

    @app.post("/settings/{key}")
    def settings_save(request: Request, key: str, value: str = Form(""), csrf: str = Form("")):
        sess = check_csrf(request, csrf)
        require(request, "administrator")
        if key not in config.RUNTIME_SETTINGS:
            raise Denied("Неизвестная настройка.")
        conn = connect()
        try:
            with transaction(conn):
                try:
                    old, new = settings_repo.set_value_audited(conn, key, value,
                                                               author=sess["username"])
                except ValueError as exc:
                    flash(request, f"Не сохранено: {exc}", "error")
                    return back(f"/settings#{key}")
                users_repo.audit(conn, "setting_changed", actor_user_id=sess["user_id"],
                                 actor=sess["username"], ip=client_ip(request), target=key,
                                 old=old, new=new)
        finally:
            conn.close()
        flash(request, f"Сохранено: {key}")
        return back(f"/settings#{key}")


# --- Панель администратора ----------------------------------------------------------

def _routes_admin(app: FastAPI) -> None:
    @app.get("/admin/users", response_class=HTMLResponse)
    def users(request: Request, query: str = "", status: str = ""):
        require(request, "administrator")
        conn = connect()
        try:
            rows = [dict(r) for r in users_repo.listing(conn, query=query, status=status)]
        finally:
            conn.close()
        return render(request, "admin_users.html", rows=_local_times(rows), query=query,
                      status=status)

    @app.post("/admin/users")
    def user_create(request: Request, email: str = Form(...), role: str = Form(""),
                    csrf: str = Form("")):
        check_csrf(request, csrf)
        require(request, "administrator")
        email = email.strip().lower()
        username = security.username_from_email(email)
        error = _user_email_error(email, username)
        role_value = role if role in config.ROLES else None
        if not error:
            conn = connect()
            try:
                with transaction(conn):
                    if users_repo.by_username(conn, username) is not None:
                        error = f"Имя «{username}» уже занято."
                    else:
                        users_repo.create(conn, username=username, email=email, role=role_value)
            finally:
                conn.close()
        if error:
            flash(request, error, "error")
        else:
            _audit(request, "user_created", target=username, new={"email": email, "role": role_value})
            flash(request, f"Пользователь {username} создан; роль сохранится при первом входе.")
        return back("/admin/users")

    @app.post("/admin/users/{user_id}")
    def user_update(request: Request, user_id: int, action: str = Form(...),
                    email: str = Form(""), role: str = Form(""), csrf: str = Form("")):
        check_csrf(request, csrf)
        require(request, "administrator")
        conn = connect()
        try:
            with transaction(conn):
                user = users_repo.get(conn, user_id)
                if user is None:
                    raise Denied("Пользователь не найден.")
                if user["is_local_admin"]:
                    raise Denied("admin нельзя заблокировать, удалить, понизить или изменить.")
                message, audit = _apply_user_action(conn, user, action, email=email, role=role)
        finally:
            conn.close()
        if audit:
            _audit(request, audit[0], target=user["username"], old=audit[1], new=audit[2])
        flash(request, message, "ok" if audit else "error")
        return back("/admin/users")

    @app.get("/admin/sessions", response_class=HTMLResponse)
    def all_sessions(request: Request):
        require(request, "administrator")
        conn = connect()
        try:
            rows = [dict(r) for r in users_repo.sessions_listing(conn)]
        finally:
            conn.close()
        return render(request, "sessions.html", rows=_local_times(rows), mine=False)

    @app.post("/admin/sessions/{session_id}/delete")
    def admin_session_delete(request: Request, session_id: int, csrf: str = Form("")):
        check_csrf(request, csrf)
        require(request, "administrator")
        _drop_session(request, session_id)
        return back("/admin/sessions")

    @app.post("/admin/users/{user_id}/sessions/delete")
    def admin_user_sessions_delete(request: Request, user_id: int, csrf: str = Form("")):
        check_csrf(request, csrf)
        require(request, "administrator")
        conn = connect()
        try:
            with transaction(conn):
                user = users_repo.get(conn, user_id)
                count = users_repo.delete_user_sessions(conn, user_id)
        finally:
            conn.close()
        _audit(request, "sessions_deleted", target=user["username"] if user else str(user_id),
               new={"count": count})
        flash(request, f"Удалено сессий: {count}")
        return back("/admin/sessions")

    @app.get("/admin/audit", response_class=HTMLResponse)
    def audit(request: Request, action: str = ""):
        require(request, "administrator")
        conn = connect()
        try:
            rows = [dict(r) for r in users_repo.audit_listing(conn, action=action)]
            actions_list = users_repo.audit_actions(conn)
        finally:
            conn.close()
        return render(request, "admin_audit.html", rows=_local_times(rows), action=action,
                      actions=actions_list)


def _user_email_error(email: str, username: str) -> str:
    if "@" not in email or not username:
        return "Нужен email."
    if not security.domain_allowed(email):
        return "Email не из разрешённого домена (Настройки → Вход)."
    if username == security.ADMIN_USERNAME:
        return "Имя admin зарезервировано."
    return ""


def _apply_user_action(conn, user, action: str, *, email: str, role: str):
    """Правка пользователя администратором. Возвращает (сообщение, (событие, было, стало) | None)."""
    user_id, username = user["id"], user["username"]
    if action == "role":
        new_role = role if role in config.ROLES else None
        users_repo.update(conn, user_id, role=new_role)
        return f"Роль {username}: {new_role or 'без роли'}", ("user_role_changed", user["role"], new_role)
    if action == "block":
        users_repo.update(conn, user_id, status="blocked")
        dropped = users_repo.delete_user_sessions(conn, user_id)
        return (f"{username} заблокирован, удалено сессий: {dropped}",
                ("user_blocked", user["status"], "blocked"))
    if action == "unblock":
        status = "active" if user["yandex_id"] else "created"
        users_repo.update(conn, user_id, status=status)
        return f"{username} разблокирован", ("user_unblocked", "blocked", status)
    if action == "email":
        email = email.strip().lower()
        new_name = security.username_from_email(email)
        error = _user_email_error(email, new_name)
        if error:
            return error, None
        clash = users_repo.by_username(conn, new_name)
        if clash is not None and clash["id"] != user_id:
            return f"Имя «{new_name}» уже занято.", None
        users_repo.update(conn, user_id, email=email, username=new_name)
        return f"Email {username} → {email}", ("user_email_changed", user["email"], email)
    if action == "delete":
        users_repo.delete_user_sessions(conn, user_id)
        users_repo.delete(conn, user_id)
        return f"{username} удалён", ("user_deleted", user["email"], None)
    return "Неизвестное действие.", None
