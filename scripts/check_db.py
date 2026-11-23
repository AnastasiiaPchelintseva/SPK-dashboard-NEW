"""Проверка данных в базе: python -m scripts.check_db

Показывает, сколько живых документов в каждой коллекции и сколько пользователей.
Запускается внутри контейнера приложения (DB_* из окружения).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spk.database import connect  # noqa: E402

conn = connect()
try:
    rows = conn.execute(
        "SELECT collection, COUNT(*) AS c FROM documents WHERE deleted = FALSE "
        "GROUP BY collection ORDER BY collection;"
    ).fetchall()
    print("документы:", ", ".join(f"{r['collection']}: {r['c']}" for r in rows))
    print("seq:", conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM documents;").fetchone()["s"])
    print("пользователей:", conn.execute("SELECT COUNT(*) AS c FROM users;").fetchone()["c"])
    print("настроек:", conn.execute("SELECT COUNT(*) AS c FROM settings;").fetchone()["c"])
finally:
    conn.close()
