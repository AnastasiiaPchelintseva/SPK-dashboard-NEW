"""spk/repositories/documents_repo.py — данные дашборда (таблица documents).

Те же документы, что были в базе артефакта Claude (`claude.use("db")`): коллекции state,
nzp, nzd, brk, wk, rsu, msd (dashboard-status.md, «Данные в базе»).

`seq` — сквозной номер правки из последовательности `documents_seq`. Открытая страница
запоминает последний полученный seq и забирает только то, что изменилось после него
(`changes`). Удалённый документ остаётся строкой с `deleted = TRUE`, чтобы удаление дошло
до открытых страниц.

Запись — только внутри `transaction(conn)` (общий замок): номера правок идут без пропусков
и перестановок. `if_version` — защита от затирания чужой правки: страница присылает версию,
которую видела; не совпала — 409.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from spk import config
from spk.database import Connection, Row


class VersionConflict(Exception):
    """Документ изменился с тех пор, как страница его прочитала."""

    def __init__(self, current: Row | None):
        super().__init__("документ изменился")
        self.current = current


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_collection(collection: str) -> None:
    if collection not in config.COLLECTIONS:
        raise KeyError(f"неизвестная коллекция {collection!r}")


def _row(row: Row | None) -> dict | None:
    if row is None:
        return None
    data = row["data"]
    if isinstance(data, str):
        data = json.loads(data)
    return {
        "collection": row["collection"],
        "id": row["doc_id"],
        "data": data,
        "deleted": bool(row["deleted"]),
        "version": row["version"],
        "seq": row["seq"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def get(conn: Connection, collection: str, doc_id: str) -> dict | None:
    _check_collection(collection)
    row = conn.execute(
        "SELECT * FROM documents WHERE collection = ? AND doc_id = ?;", (collection, doc_id)
    ).fetchone()
    return _row(row)


def collection(conn: Connection, name: str) -> list[dict]:
    """Все живые документы коллекции (удалённые не отдаются)."""
    _check_collection(name)
    rows = conn.execute(
        "SELECT * FROM documents WHERE collection = ? AND deleted = FALSE ORDER BY doc_id;",
        (name,),
    ).fetchall()
    return [_row(r) for r in rows]


def all_collections(conn: Connection) -> dict[str, list[dict]]:
    """Снимок всех коллекций — для первого открытия страницы."""
    result: dict[str, list[dict]] = {name: [] for name in config.COLLECTIONS}
    for row in conn.execute(
        "SELECT * FROM documents WHERE deleted = FALSE ORDER BY collection, doc_id;"
    ):
        result.setdefault(row["collection"], []).append(_row(row))
    return result


def max_seq(conn: Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM documents;").fetchone()
    return int(row["s"])


def changes(conn: Connection, since: int, limit: int = 5000) -> list[dict]:
    """Всё, что изменилось после `since` (включая удаления), по возрастанию seq."""
    rows = conn.execute(
        "SELECT * FROM documents WHERE seq > ? ORDER BY seq LIMIT ?;", (since, limit)
    ).fetchall()
    return [_row(r) for r in rows]


def _next_seq(conn: Connection) -> int:
    return int(conn.execute("SELECT nextval('documents_seq');").fetchone()[0])


def put(
    conn: Connection, collection: str, doc_id: str, data, *,
    if_version: int | None = None, author: str = "",
) -> dict:
    """Записать документ. `if_version` — версия, которую видела страница (None — без проверки).

    Должно вызываться внутри `transaction(conn)`.
    """
    _check_collection(collection)
    current = conn.execute(
        "SELECT * FROM documents WHERE collection = ? AND doc_id = ?;", (collection, doc_id)
    ).fetchone()
    if if_version is not None:
        seen = 0 if current is None else int(current["version"])
        if seen != int(if_version):
            raise VersionConflict(current)
    seq = _next_seq(conn)
    version = 1 if current is None else int(current["version"]) + 1
    conn.execute(
        "INSERT INTO documents (collection, doc_id, data, deleted, seq, version, updated_at, "
        "updated_by) VALUES (?, ?, ?::jsonb, FALSE, ?, ?, ?, ?) "
        "ON CONFLICT (collection, doc_id) DO UPDATE SET data = excluded.data, deleted = FALSE, "
        "seq = excluded.seq, version = excluded.version, updated_at = excluded.updated_at, "
        "updated_by = excluded.updated_by;",
        (collection, doc_id, json.dumps(data, ensure_ascii=False), seq, version, _utc_now(),
         author),
    )
    return get(conn, collection, doc_id)


def delete(
    conn: Connection, collection: str, doc_id: str, *,
    if_version: int | None = None, author: str = "",
) -> dict | None:
    """Пометить документ удалённым (строка остаётся, чтобы удаление дошло до страниц)."""
    _check_collection(collection)
    current = conn.execute(
        "SELECT * FROM documents WHERE collection = ? AND doc_id = ?;", (collection, doc_id)
    ).fetchone()
    if current is None:
        return None
    if if_version is not None and int(current["version"]) != int(if_version):
        raise VersionConflict(current)
    seq = _next_seq(conn)
    conn.execute(
        "UPDATE documents SET data = NULL, deleted = TRUE, seq = ?, version = version + 1, "
        "updated_at = ?, updated_by = ? WHERE collection = ? AND doc_id = ?;",
        (seq, _utc_now(), author, collection, doc_id),
    )
    return get(conn, collection, doc_id)


def stats(conn: Connection) -> dict[str, int]:
    """Сколько живых документов в каждой коллекции (для панели администратора)."""
    result = {name: 0 for name in config.COLLECTIONS}
    for row in conn.execute(
        "SELECT collection, COUNT(*) AS c FROM documents WHERE deleted = FALSE GROUP BY collection;"
    ):
        result[row["collection"]] = int(row["c"])
    return result
