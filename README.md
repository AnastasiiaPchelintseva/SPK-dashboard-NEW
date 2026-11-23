# Дашборд СПК

Производственный дашборд цехов ПВХ и Алюминий (план/факт, НЗП, отчёт ДД, недельный отчёт, двери/окна по бригадам, Excel «выработка на человека»).

- `dashboard.html` — вся страница одним файлом (версия 101). Данные — через API сервиса (`spk/api.py`), библиотеки раздаются самим сервисом из `spk/static/vendor/`.
- `spk/` — сервис на Python: FastAPI + Jinja2, PostgreSQL, вход через Яндекс / `admin` по [PLATFORM.md](PLATFORM.md).
- `AGENTS.md` — описание проекта, структура, команды и правила; `CLAUDE.md` — договорённости с пользователем; `dashboard-status.md` — правила расчёта и схема данных.
- `db/` — выгрузка базы артефакта на 09.11.2026 (`sample-data/` — её копия); переносится в PostgreSQL командой `python -m scripts.import_db`.
- `source-files/` — исходные файлы 1С и Excel (см. `source-files/README.md`).

Репозиторий закрытый: в нём реальные данные производства.

## Запуск

```bash
python -m pip install -r requirements.txt
cp .env.example .env                 # DB_*, ADMIN_PASSWORD, YANDEX_*
python -m spk.migrate                # схема базы
python -m scripts.import_db          # разово: выгрузка db/ → PostgreSQL
python -m uvicorn spk.web:create_app --factory --port 8000
```

В Docker — `docker compose -p spk_dashboard -f docker-compose.server.yml up -d --build`.
Публикация на сервер команды — `ops/deploy.sh SPK-dashboard` из общего репозитория.
