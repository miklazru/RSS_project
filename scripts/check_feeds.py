"""Проверка сборщика на реальных лентах (нужен интернет).

    python -m scripts.check_feeds

Для каждой ленты: скачалась ли, сколько записей, сколько из них без даты / без описания,
сколько времени ушло. Ничего не пишет в БД.
"""
import sys
import time

from app.fetcher import download_feed
from scripts.feeds import FEEDS


def main() -> int:
    failed = 0
    print(f"{'канал':26} {'статус':7} {'записей':>7} {'без даты':>8} {'без текста':>10} {'сек':>5}  название / ошибка")
    for name, url in FEEDS:
        started = time.monotonic()
        result = download_feed(url)
        seconds = time.monotonic() - started
        if result.error:
            failed += 1
            print(f"{name:26} {'FAIL':7} {'-':>7} {'-':>8} {'-':>10} {seconds:5.1f}  {result.error[:110]}")
            continue
        no_date = sum(1 for item in result.items if item.published_at is None)
        no_text = sum(1 for item in result.items if not item.summary)
        print(
            f"{name:26} {'ok':7} {len(result.items):7} {no_date:8} {no_text:10} {seconds:5.1f}  {result.title}"
        )
    print(f"\nработает {len(FEEDS) - failed} из {len(FEEDS)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
