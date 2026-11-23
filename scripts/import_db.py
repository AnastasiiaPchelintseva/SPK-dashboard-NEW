"""scripts/import_db.py — разовый перенос выгрузки базы артефакта (`db/`) в PostgreSQL.

Выгрузка — по файлу на документ: `<коллекция>/<id>.json` (см. `db/README.md`).
Документы пишутся через `documents_repo.put` внутри одной транзакции: seq идёт по порядку,
повторный запуск перезаписывает те же документы (версия растёт).

    python -m scripts.import_db                 # из db/ рядом с проектом
    python -m scripts.import_db --dir sample-data --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spk import config  # noqa: E402
from spk.database import connect, transaction  # noqa: E402
from spk.migrate import apply_migrations  # noqa: E402
from spk.repositories import documents_repo  # noqa: E402


def read_dump(base: Path) -> list[tuple[str, str, object]]:
    """Все документы выгрузки: (коллекция, id, данные). Служебные файлы (README, .*) пропускаются."""
    result = []
    for collection in config.COLLECTIONS:
        folder = base / collection
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if path.name.startswith("."):
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append((collection, path.stem, data))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Перенос выгрузки базы дашборда в PostgreSQL")
    parser.add_argument("--dir", default=str(ROOT / "db"), help="папка выгрузки (по умолчанию db/)")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет записано")
    parser.add_argument("--author", default="import", help="кто записал (в documents.updated_by)")
    args = parser.parse_args()

    base = Path(args.dir)
    if not base.is_dir():
        print(f"нет папки {base}", file=sys.stderr)
        return 2
    docs = read_dump(base)
    counts: dict[str, int] = {}
    for collection, _, _ in docs:
        counts[collection] = counts.get(collection, 0) + 1
    print(f"документов: {len(docs)} — " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    if args.dry_run:
        return 0

    apply_migrations()
    conn = connect()
    try:
        with transaction(conn):
            for collection, doc_id, data in docs:
                documents_repo.put(conn, collection, doc_id, data, author=args.author)
        print(f"записано, последний seq = {documents_repo.max_seq(conn)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
