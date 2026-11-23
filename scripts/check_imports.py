"""Проверка импорта приложения: python -m scripts.check_imports"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import spk.api  # noqa: E402,F401
import spk.config  # noqa: E402,F401
import spk.database  # noqa: E402,F401
import spk.migrate  # noqa: E402,F401
import spk.security  # noqa: E402,F401
import spk.web  # noqa: E402,F401
import spk.web_common  # noqa: E402,F401
from spk.repositories import documents_repo, settings_repo, users_repo  # noqa: E402,F401

print("imports OK")
