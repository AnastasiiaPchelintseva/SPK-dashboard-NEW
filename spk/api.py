"""spk/api.py — API данных дашборда вместо `window.claude` (база артефакта Claude).

Страница (`dashboard.html`) обращается к этим адресам через прослойку `window.claude`:

    GET  /api/db                      снимок всех коллекций + seq (первое открытие)
    GET  /api/db/<коллекция>          все живые документы коллекции
    GET  /api/db/changes?since=<seq>  что изменилось после seq (включая удаления)
    PUT  /api/db/<коллекция>/<id>     записать документ {data, if_version?}
    DELETE /api/db/<коллекция>/<id>   пометить удалённым (?if_version=)

Запись — только роли `editor` и выше (PLATFORM.md, раздел 7). `if_version` защищает от
затирания чужой правки: не совпала — 409 и текущий документ в ответе.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from spk import config
from spk.database import connect, transaction
from spk.repositories import documents_repo
from spk.web_common import client_ip, require, sess_of

router = APIRouter(prefix="/api")


def _error(status: int, text: str, **extra) -> JSONResponse:
    return JSONResponse({"error": text, **extra}, status_code=status)


@router.get("/db")
def snapshot(request: Request):
    require(request)
    conn = connect()
    try:
        return {"seq": documents_repo.max_seq(conn), "collections": documents_repo.all_collections(conn)}
    finally:
        conn.close()


@router.get("/db/changes")
def changes(request: Request, since: int = 0):
    require(request)
    conn = connect()
    try:
        rows = documents_repo.changes(conn, since)
        return {"seq": documents_repo.max_seq(conn), "documents": rows}
    finally:
        conn.close()


@router.get("/db/{collection}")
def collection(request: Request, collection: str):
    require(request)
    if collection not in config.COLLECTIONS:
        return _error(404, f"неизвестная коллекция {collection}")
    conn = connect()
    try:
        return {"documents": documents_repo.collection(conn, collection)}
    finally:
        conn.close()


@router.put("/db/{collection}/{doc_id:path}")
def put(request: Request, collection: str, doc_id: str, body: dict = Body(...)):
    sess = require(request, "editor")
    if collection not in config.COLLECTIONS:
        return _error(404, f"неизвестная коллекция {collection}")
    if "data" not in body:
        return _error(400, "нужно поле data")
    if_version = body.get("if_version")
    conn = connect()
    try:
        with transaction(conn):
            try:
                doc = documents_repo.put(
                    conn, collection, doc_id, body["data"],
                    if_version=if_version, author=sess["username"],
                )
            except documents_repo.VersionConflict as exc:
                current = documents_repo._row(exc.current)
                return _error(409, "документ изменился, перечитайте его", current=current)
        return doc
    finally:
        conn.close()


@router.delete("/db/{collection}/{doc_id:path}")
def delete(request: Request, collection: str, doc_id: str, if_version: int | None = None):
    sess = require(request, "editor")
    if collection not in config.COLLECTIONS:
        return _error(404, f"неизвестная коллекция {collection}")
    conn = connect()
    try:
        with transaction(conn):
            try:
                doc = documents_repo.delete(
                    conn, collection, doc_id, if_version=if_version, author=sess["username"],
                )
            except documents_repo.VersionConflict as exc:
                current = documents_repo._row(exc.current)
                return _error(409, "документ изменился, перечитайте его", current=current)
        return {"deleted": doc is not None, "document": doc}
    finally:
        conn.close()


@router.get("/csrf")
def csrf(request: Request):
    """Токен для форм, которые страница дашборда отправляет сама (выход)."""
    sess = sess_of(request)
    if sess is None:
        return _error(401, "нужен вход")
    return {"csrf": sess["csrf_token"]}


@router.get("/me")
def me(request: Request):
    sess = sess_of(request)
    if sess is None:
        return _error(401, "нужен вход")
    return {
        "username": sess["username"],
        "display_name": sess["display_name"],
        "role": sess["role"],
        "can_edit": bool(sess["role"] and sess["role"] != "viewer"),
        "sync_interval_s": config.SYNC_INTERVAL_S,
        "ip": client_ip(request),
    }
