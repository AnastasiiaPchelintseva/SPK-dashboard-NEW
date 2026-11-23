"""Диагностика входа admin: python -m scripts.check_admin

Показывает пользователей и проверяет, совпадает ли хэш admin с ADMIN_PASSWORD из окружения.
Пароль не печатается.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spk import config  # noqa: E402
from spk.database import connect  # noqa: E402
from spk.security import _verify  # noqa: E402

print("ADMIN_PASSWORD задан:", bool(config.ADMIN_PASSWORD))
print("длина ADMIN_PASSWORD:", len(config.ADMIN_PASSWORD or ""))

conn = connect()
try:
    rows = conn.execute(
        "SELECT username, role, status, is_local_admin, "
        "password_hash IS NOT NULL AS has_hash FROM users ORDER BY id;"
    ).fetchall()
    for row in rows:
        print(dict(row))
    admin = conn.execute(
        "SELECT * FROM users WHERE is_local_admin = 1;"
    ).fetchone()
    if admin is None:
        print("admin НЕ найден в базе")
    else:
        print("admin найден, id =", admin["id"])
        print("хэш есть:", bool(admin["password_hash"]))
        if admin["password_hash"] and config.ADMIN_PASSWORD:
            print("пароль из .env подходит:", _verify(admin["password_hash"], config.ADMIN_PASSWORD))
finally:
    conn.close()
