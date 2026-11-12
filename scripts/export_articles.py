"""Выгрузка реальных статей в JSON для ролей 3 и 4 (тесты ключевых слов, подбор порогов тем).

    python -m scripts.export_articles                      # 200 статей -> data/articles_export.json
    python -m scripts.export_articles --count 300 --out my.json

Нужен интернет, сервер и база не нужны. Статьи берутся из лент scripts/feeds.py по кругу
(сначала самые свежие из каждой ленты), поэтому в выгрузке перемешаны разные источники и темы.
Дубли по заголовку и слишком короткие заголовки отбрасываются.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from app.fetcher import download_feed
from app.pipeline import FeedItem
from scripts.feeds import FEEDS, NOT_NEWS

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "articles_export.json"
MIN_TITLE_LENGTH = 10


def _normalize(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def _newest_first(items: List[FeedItem]) -> List[FeedItem]:
    """Свежие вперёд; записи без даты — в конец, в исходном порядке ленты."""
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(items, key=lambda item: item.published_at or epoch, reverse=True)


def pick_articles(per_channel: Dict[str, List[FeedItem]], count: int) -> List[dict]:
    """Выбирает count статей по кругу из каналов, без дублей по заголовку."""
    queues = {name: _newest_first(items) for name, items in per_channel.items()}
    seen, picked = set(), []
    while len(picked) < count and any(queues.values()):
        for name, queue in queues.items():
            while queue:
                item = queue.pop(0)
                key = _normalize(item.title)
                if len(item.title) < MIN_TITLE_LENGTH or key in seen:
                    continue
                seen.add(key)
                picked.append(
                    {
                        "channel": name,
                        "title": item.title,
                        "summary": item.summary,
                        "link": item.link,
                        "published_at": item.published_at.isoformat() if item.published_at else None,
                    }
                )
                break
            if len(picked) >= count:
                break
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=200, help="сколько статей выгрузить")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="куда писать JSON")
    args = parser.parse_args()

    per_channel: Dict[str, List[FeedItem]] = {}
    for name, url in FEEDS:
        if name in NOT_NEWS:
            continue
        result = download_feed(url)
        if result.error:
            print(f"пропускаю {name}: {result.error[:100]}", file=sys.stderr)
            continue
        per_channel[name] = result.items
        print(f"{name}: {len(result.items)} записей")

    articles = pick_articles(per_channel, args.count)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")

    with_text = sum(1 for article in articles if article["summary"])
    channels = len({article["channel"] for article in articles})
    print(f"\nвыгружено {len(articles)} статей из {channels} каналов, с описанием: {with_text} -> {args.out}")
    return 0 if articles else 1


if __name__ == "__main__":
    sys.exit(main())
