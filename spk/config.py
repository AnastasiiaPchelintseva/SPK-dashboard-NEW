"""spk/config.py — параметры приложения (PLATFORM.md, раздел 2).

Из окружения (`.env`) берутся только ADMIN_PASSWORD, DB_* и YANDEX_*. Приложение запускается и
без них: без базы — страница «Не настроено», без ключей Яндекса — работает только `admin`.
Всё остальное — таблица `settings`, правится в панели администратора («Настройки»); здесь
только описание и умолчания (`RUNTIME_SETTINGS`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """`.env` в корне проекта (локальный запуск). Уже заданные переменные не перекрываются."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# --- .env (PLATFORM.md, раздел 2) ----------------------------------------------------

DB_HOST = _env("DB_HOST", "localhost")
DB_PORT = _env_int("DB_PORT", 5432)
DB_NAME = _env("DB_NAME", "spk_dashboard")
DB_USER = _env("DB_USER", "spk_dashboard")
DB_PASSWORD = _env("DB_PASSWORD")

ADMIN_PASSWORD = _env("ADMIN_PASSWORD")
YANDEX_CLIENT_ID = _env("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = _env("YANDEX_CLIENT_SECRET")

# --- Постоянные ------------------------------------------------------------------------

APP_TITLE = "Дашборд СПК"
SESSION_COOKIE_NAME = "spk_dashboard_session"
STATE_COOKIE_NAME = "spk_dashboard_state"
SESSION_TTL_DAYS = 7
LOCAL_TZ = "Europe/Moscow"

DASHBOARD_FILE = ROOT / "dashboard.html"
STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

#: Коллекции базы дашборда (dashboard-status.md, «Данные в базе»). Другие страница не пишет.
COLLECTIONS = ("state", "nzp", "nzd", "brk", "wk", "rsu", "msd")

#: Роли проекта (PLATFORM.md, раздел 7). Порядок — от младшей к старшей.
ROLES = {
    "viewer": "просмотр дашборда, сохранение PDF и Excel",
    "editor": "то же + загрузка отчётов 1С и ввод данных (запись в базу)",
    "administrator": "полный доступ: то же + панель администратора",
}


# --- Настройки в базе (панель администратора) ---------------------------------------------

@dataclass(frozen=True)
class RuntimeSetting:
    title: str
    kind: str  # text | int
    help: str = ""
    bounds: tuple[int, int] | None = None


RUNTIME_SETTINGS: dict[str, RuntimeSetting] = {
    "ALLOWED_EMAIL_DOMAINS": RuntimeSetting(
        "Разрешённые домены почты", "text",
        "через запятую; поддомены не проходят (PLATFORM.md, раздел 4)",
    ),
    "SYNC_INTERVAL_S": RuntimeSetting(
        "Как часто дашборд забирает чужие изменения, с", "int",
        "у открытой страницы; свои изменения видны сразу", bounds=(5, 600),
    ),
}

RUNTIME_DEFAULTS: dict[str, object] = {
    "ALLOWED_EMAIL_DOMAINS": "pik.ru, pik-industry.ru, prefabtech.ru, pik-element.ru",
    "SYNC_INTERVAL_S": 20,
}

ALLOWED_EMAIL_DOMAINS: str = RUNTIME_DEFAULTS["ALLOWED_EMAIL_DOMAINS"]  # type: ignore[assignment]
SYNC_INTERVAL_S: int = RUNTIME_DEFAULTS["SYNC_INTERVAL_S"]  # type: ignore[assignment]


def settings_problems() -> list[str]:
    """Чего не хватает для работы (видно администратору вверху страниц панели)."""
    problems = []
    if not ALLOWED_EMAIL_DOMAINS.strip():
        problems.append("разрешённые домены почты пусты — через Яндекс не войдёт никто")
    if not (YANDEX_CLIENT_ID and YANDEX_CLIENT_SECRET):
        problems.append("вход через Яндекс выключен: не заданы YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET")
    return problems
