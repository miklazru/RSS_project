"""Настройки приложения. Всё задаётся переменными окружения, у всего есть значения по умолчанию."""
import os
from pathlib import Path

# Где лежит база. В Docker папка data монтируется как том, чтобы данные переживали перезапуск.
DB_PATH = Path(os.environ.get("DB_PATH", "data/app.db"))

# Как часто сборщик обходит все каналы, в секундах.
FETCH_INTERVAL_SECONDS = int(os.environ.get("FETCH_INTERVAL_SECONDS", "60"))

# Выключатель фонового сборщика (в тестах он не нужен).
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") == "1"

# Таймаут на скачивание одной ленты, в секундах.
FETCH_TIMEOUT_SECONDS = float(os.environ.get("FETCH_TIMEOUT_SECONDS", "15"))

# Адрес, по которому сервис доступен снаружи. Нужен, чтобы в /rss/test.xml были правильные ссылки.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")

# Порог сходства для тем по умолчанию (если пользователь не указал свой).
DEFAULT_TOPIC_THRESHOLD = float(os.environ.get("DEFAULT_TOPIC_THRESHOLD", "0.5"))
