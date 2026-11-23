<!-- Копия. Источник — platform-status/SPK-dashboard.md в общем репозитории vibecodeprojects
     (https://git.pik.ru/application-department/vibecodeprojects). Здесь не править: состояние ведётся
     там и раскладывается командой ops/sync-platform.sh. Замечание исправлено или есть вопрос —
     сообщите тому, кто ведёт общий репозиторий. -->

# SPK-dashboard: соответствие PLATFORM.md

Стек Python 3.12: FastAPI + Jinja2, PostgreSQL (SQLAlchemy + Alembic), вход через Яндекс (Authlib)
или локальный `admin`. Сверено с кодом 2026-11-23, после коммита `1430179` в
AnastasiiaPchelintseva/SPK-dashboard-NEW (ветка `main`). Переход сделан: страница `dashboard.html`
ходит в API сервиса (`spk/api.py`) вместо `claude.use("db")`, библиотеки раздаются самим сервисом.
Тесты при сверке не запускались: на машине сверки нет Python.

Обозначения: ✅ соответствует · ⚠️ частично · ❌ нет.

| Раздел | Требование | Сейчас | |
|---|---|---|---|
| 1 | Стек Python или .NET | Python 3.12, FastAPI + Jinja2 | ✅ |
| 1 | Веб-интерфейс не на Streamlit | FastAPI + Jinja2 (`spk/web.py`) | ✅ |
| 2 | Параметры — в панели администратора | таблица `settings`, страница «Настройки»: домены почты, интервал синхронизации; применяется без перезапуска | ✅ |
| 2 | В `.env` только `ADMIN_PASSWORD`, `DB_*`, `YANDEX_*` | да | ✅ |
| 2 | Запуск с пустым `.env`, режим «Не настроено» без БД | `Readiness`: страница «Не настроено», повтор раз в 30 с, миграции после подключения | ✅ |
| 2 | Запуск без ключей Яндекса | вход через Яндекс выключен, работает `admin` | ✅ |
| 2 | Деплой требует все переменные раздела 2 | `ops/deploy.sh` (`PLATFORM_VARS`) | ✅ |
| 3 | Своя база и пользователь PostgreSQL, миграции при старте | `spk_dashboard`/`spk_dashboard`, Alembic при старте с замком (`spk/migrate.py`) | ✅ |
| 3 | Ключи подписи cookie в БД | таблица `app_keys` (`users_repo.app_key`) | ✅ |
| 4 | Пользователь по Яндексу `id`, `username` = часть email, `admin` зарезервировано | `spk/security.py` | ✅ |
| 4 | Авторегистрация с разрешённых доменов, домены — настройка | `ALLOWED_EMAIL_DOMAINS` в настройках | ✅ |
| 4 | Создание по email, привязка при первом входе, конфликт имён | есть | ✅ |
| 4 | Состояния, блокировка с удалением сессий, «Доступ заблокирован» | есть | ✅ |
| 4 | Смена email администратором | `/admin/users/{id}` (action=email) | ✅ |
| 5 | Яндекс: PKCE, `force_confirm=yes`, `/signin-yandex` | Authlib | ✅ |
| 5 | Локальный `admin`: пароль из `.env`, argon2, 5 попыток за 15 мин | есть, счётчик по журналу аудита | ✅ |
| 5 | Вход для разработки | нет | ✅ |
| 6 | Серверные сессии, хэш токена, 7 дней, касание раз в минуту, IP, User-Agent | `sessions`, IP из `X-Forwarded-For` | ✅ |
| 6 | Cookie `HttpOnly`, `Lax`, `Secure` по схеме, скользящий срок | uvicorn `--proxy-headers` | ✅ |
| 6 | Свои сессии, выход удаляет сессию | `/account/sessions`, `/logout` | ✅ |
| 6 | Защита POST от подделки запросов | csrf-токен сессии во всех формах | ✅ |
| 7 | Роли, новый пользователь без роли | `viewer < editor < administrator`; без роли — «Доступ не выдан», описано в `AGENTS.md` | ✅ |
| 8 | Панель: пользователи, сессии, настройки, журнал | поиск, фильтр, создание, email, роль, блокировка, удаление; все сессии пользователя | ✅ |
| 9 | События аудита | `audit_log` | ✅ |
| 12 | В корне: `AGENTS.md`, `PLATFORM.md`, `PLATFORM-STATUS.md`, `docker-compose.server.yml`, `.env.example` | есть | ✅ |
| 12 | `docker-compose.server.yml` по шаблону | `build:` + `${SPK_DASHBOARD_IMAGE}`, переменные раздела 2, `host.docker.internal`, `${APP_BIND}:${APP_PORT:-8600}`, `restart: unless-stopped` | ✅ |
| 12 | За внешним nginx: корень адреса, `X-Forwarded-For`/`-Proto`, без своего HTTPS-редиректа | `--proxy-headers`, `client_ip` из `X-Forwarded-For` | ✅ |
| 12 | Логи в stdout, проверка готовности | uvicorn в stdout; `/health/ready` | ✅ |

## Что может пользователь без роли

Экран «Доступ не выдан, обратитесь к администратору» и свои сессии (`AGENTS.md`).

## Что осталось

По коду — ничего. Не сделано на стороне общего репозитория: проект добавлен в `ops/deploy.sh`,
`ops/update.sh`, `DEPLOY.md`, `README.md`, `SYNC.md` и `ops/secrets.example/SPK-dashboard.env`;
осталось заполнить `.secrets/SPK-dashboard.env` (порт 8600, `ADMIN_PASSWORD`, `DB_*`, `YANDEX_*`),
выполнить `ops/deploy.sh setup` и первую выкладку `ops/deploy.sh SPK-dashboard`.
