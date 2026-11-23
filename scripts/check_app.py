"""Проверка запуска приложения без базы: python -m scripts.check_app

Приложение должно создаться и работать в режиме «Не настроено» (PLATFORM.md, раздел 2),
а не упасть. База не нужна.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spk.web import create_app  # noqa: E402

app = create_app()
paths = sorted({getattr(r, "path", "") for r in app.routes})
print("app OK, routes:", len(paths))
for path in paths:
    print(" ", path)
