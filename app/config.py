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

# Сколько раз повторять скачивание при сетевой ошибке, 429 и 5xx (кроме первой попытки).
FETCH_RETRIES = int(os.environ.get("FETCH_RETRIES", "2"))

# Пауза перед первым повтором, в секундах; каждый следующий повтор ждёт вдвое дольше.
FETCH_BACKOFF_SECONDS = float(os.environ.get("FETCH_BACKOFF_SECONDS", "1"))

# Максимальный размер ответа с лентой, в байтах (по умолчанию 5 МБ).
FETCH_MAX_BYTES = int(os.environ.get("FETCH_MAX_BYTES", str(5 * 1024 * 1024)))

# Сколько записей брать из одной ленты за раз (в лентах идут сначала новые).
FETCH_MAX_ENTRIES = int(os.environ.get("FETCH_MAX_ENTRIES", "200"))

# Сколько каналов обходить одновременно.
FETCH_WORKERS = int(os.environ.get("FETCH_WORKERS", "4"))

# Адрес, по которому сервис доступен снаружи. Нужен, чтобы в /rss/test.xml были правильные ссылки.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")

# Порог сходства для тем по умолчанию (если пользователь не указал свой).
DEFAULT_TOPIC_THRESHOLD = float(os.environ.get("DEFAULT_TOPIC_THRESHOLD", "0.5"))
