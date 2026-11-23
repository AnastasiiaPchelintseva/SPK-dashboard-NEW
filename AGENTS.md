# Дашборд СПК (SPK-dashboard) — AGENTS.md

Дашборд производства СПК: план/факт и НЗП по двум цехам (ПВХ и Алюминий), всё в м².
Одна страница (`dashboard.html`) и сервис на Python, который её отдаёт и хранит данные в
PostgreSQL. Раньше страница жила как Artifact в Claude и ходила в базу артефакта
(`claude.use("db")`); теперь те же документы лежат в таблице `documents`, а страница ходит
в API сервиса (`spk/api.py`).

Стек: Python 3.12, PostgreSQL (SQLAlchemy + Alembic, SQL руками), FastAPI + Jinja2 (веб, вход по
PLATFORM.md), Docker.
Персональный репозиторий: https://github.com/AnastasiiaPchelintseva/SPK-dashboard-NEW (ветка `main`).

## Документация

1. `dashboard-status.md` — правила расчёта, схема данных, список отчётов 1С. Читать первым.
2. `CLAUDE.md` — договорённости с пользователем (что и как считается, что не менять).
3. `PLATFORM.md` — общие требования к проектам (копия, её не править).
4. `PLATFORM-STATUS.md` — сверка проекта с PLATFORM.md (копия, её не править).

## Структура

```
dashboard.html             страница дашборда (вкладки, отчёты, PDF/Excel); данные — через API
spk/config.py              параметры из .env (ADMIN_PASSWORD, DB_*, YANDEX_*), роли, настройки в базе
spk/database.py            PostgreSQL: пул SQLAlchemy, ?-параметры, transaction() с общим замком
spk/migrate.py + alembic/  схема при старте (Alembic), замок миграций
spk/repositories/          SQL руками: users (пользователи, сессии, журнал), settings, documents
spk/security.py            вход через Яндекс, локальный admin, сессии, роли
spk/web.py                 FastAPI: вход, админка, настройки, журнал, /health/ready, «Не настроено»
spk/web_common.py          адрес клиента, сессия, проверка роли и CSRF
spk/api.py                 API данных дашборда (/api/db, /api/db/changes, /api/me)
spk/templates/             шаблоны панели (base, login, admin_users, sessions, settings, audit)
spk/static/vendor/         xlsx, exceljs, html2canvas, jspdf — раздаются самим сервисом
scripts/import_db.py       разовый перенос выгрузки db/ в PostgreSQL
db/, sample-data/          выгрузка базы артефакта (по файлу на документ) — для импорта и разработки
source-files/              исходные отчёты 1С и Excel (для сверки, в образ не идут)
```

## Данные

Коллекции (как в базе артефакта): `state`, `nzp`, `nzd`, `brk`, `wk`, `rsu`, `msd`.
Структура документов — `dashboard-status.md`, раздел «Данные в базе».

- `documents(collection, doc_id, data JSONB, deleted, seq, version, updated_at, updated_by)`.
- `seq` — сквозной номер правки: открытая страница раз в `SYNC_INTERVAL_S` секунд забирает
  `/api/db/changes?since=<seq>` и применяет только изменения.
- Запись — `PUT /api/db/<коллекция>/<id>` с `if_version` (версия, которую видела страница);
  не совпала — 409, страница перечитывает документ. Удаление — `DELETE`, строка остаётся с
  `deleted = TRUE`, чтобы удаление дошло до открытых страниц.
- Писать может роль `editor` и выше; читать — любая роль.

## Вход и роли (PLATFORM.md, разделы 4–7)

Вход — через Яндекс (почта разрешённых доменов, настройка «Вход» в панели) или локальный `admin`
(пароль из `ADMIN_PASSWORD`). Сессии серверные, 7 дней с последней активности.

| Роль | Что может |
|---|---|
| `administrator` | всё: настройки, пользователи, сессии, журнал аудита |
| `editor` | дашборд + запись данных (загрузка отчётов 1С, ввод данных) |
| `viewer` | только просмотр дашборда и сохранение PDF/Excel |
| без роли | экран «Доступ не выдан, обратитесь к администратору» и свои сессии |

Новый пользователь Яндекса регистрируется сам без роли; роль выдаёт администратор.

## Команды (без Docker)

```powershell
python -m pip install -r requirements.txt
copy .env.example .env               # заполнить DB_*, ADMIN_PASSWORD; остальное — в панели
python -m spk.migrate                # схема (Alembic); база — DB_* в .env
python -m scripts.import_db          # перенести выгрузку db/ в базу (разово)
python -m uvicorn spk.web:create_app --factory --port 8000   # веб: http://127.0.0.1:8000
```

## Docker

```powershell
copy .env.example .env                 # заполнить DB_*, ADMIN_PASSWORD, YANDEX_*
$env:COMPOSE_FILE = "docker-compose.server.yml"; $env:COMPOSE_PROJECT_NAME = "spk_dashboard"
docker compose build
docker compose up -d --build
docker compose logs -f app
docker compose run --rm --no-deps app python -m scripts.import_db   # импорт выгрузки
```

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_BIND` / `APP_PORT` | `127.0.0.1` / `8600` | где публиковать веб (наружу — через nginx с HTTPS) |
| `TZ` | `Europe/Moscow` | часовой пояс для отображения (в базе всегда UTC) |
| `SPK_DASHBOARD_IMAGE` | `spk-dashboard:local` | тег собираемого образа |

## Публикация (общий репозиторий `vibecodeprojects`)

Проект подключён к общим скриптам выкладки. Всё, что нужно знать о публикации, — здесь.

| Что | Значение |
|---|---|
| compose-проект | `spk_dashboard` |
| образ | `spk-dashboard:<ревизия>`, переменная `SPK_DASHBOARD_IMAGE` |
| порт на сервере | `8600` (`APP_PORT`), наружу — внешний nginx с HTTPS |
| адрес | `https://spk-dashboard.userapp.dsk2.picompany.ru` |
| база | `spk_dashboard` / `spk_dashboard` (создаёт `ops/deploy.sh setup`) |
| ветка личного репозитория | `main` (у остальных проектов `master`) |
| проверка готовности | `/health/ready` |
| миграции | `spk/alembic/versions` |

- `ops/deploy.sh`: проект в `PROJECTS`, ветка в `project_config` (`COMPOSE_PROJECT=spk_dashboard`,
  `IMAGE_VAR=SPK_DASHBOARD_IMAGE`, `IMAGE_REPO=spk-dashboard`, `REQUIRED_VARS="$PLATFORM_VARS"`).
- `ops/update.sh`: ветка в `case "$PROJECT"` (`REMOTE=spk-dashboard`, `PORT_VAR=APP_PORT`,
  `PORT_DEFAULT=8600`, `HEALTH_PATH=/health/ready`, `MIGRATIONS_DIR=spk/alembic/versions`),
  `BRANCH=main` для этого проекта.
- `ops/secrets.example/SPK-dashboard.env` — шаблон реквизитов; рабочая копия — `.secrets/SPK-dashboard.env`.
- `DEPLOY.md` — таблицы проектов, портов и пример nginx; `README.md` — адрес и панель;
  `SYNC.md` — `git subtree push --prefix=SPK-dashboard spk-dashboard main`.
- `platform-status/SPK-dashboard.md` — сверка с PLATFORM.md; копия в проекте — `PLATFORM-STATUS.md`.

Порядок первой выкладки: заполнить `.secrets/SPK-dashboard.env` (порт 8600, `ADMIN_PASSWORD`,
`DB_*`, `YANDEX_*`) → `ops/deploy.sh setup` → `ops/deploy.sh SPK-dashboard`. Дальше —
`ops/update.sh SPK-dashboard`. Приложение на oauth.yandex.ru: Redirect URI
`https://spk-dashboard.userapp.dsk2.picompany.ru/signin-yandex`.

## Правила для правок

- Правила расчёта и договорённости с пользователем — `CLAUDE.md` и `dashboard-status.md`;
  менять их без запроса нельзя.
- Печатная форма (PDF) и Excel зафиксированы (v81, 02.11.2026) — не менять без запроса.
- Лучше пусто, чем неверно: не угадывать числа, сверять с файлами 1С и базой.
- Коммитить явные пути, не `git add -A`.
- Проект живёт в общем репозитории `vibecodeprojects` (git subtree). Синхронизация с персональным
  репозиторием — `SYNC.md` в корне общего репозитория; ветка личного репозитория — `main`.
- Общие требования ко всем проектам — [PLATFORM.md](PLATFORM.md). Это копия, её не править.
- Сверка проекта с PLATFORM.md и замечания — [PLATFORM-STATUS.md](PLATFORM-STATUS.md). Это копия,
  её не править: состояние ведётся в `platform-status/SPK-dashboard.md` общего репозитория.
