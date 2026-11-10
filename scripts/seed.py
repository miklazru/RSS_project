"""Заполняет базу демо-данными, чтобы было с чем работать до появления реальных лент.

    python -m scripts.seed

Что делает:
  - публикует статьи из data/sample_articles.json в наш тестовый RSS-канал;
  - создаёт двух пользователей с разными фильтрами (оба подписаны на тестовый канал).

Запускать на пустой базе. Повторный запуск не задублирует ни статьи, ни пользователей.
"""
import json
from pathlib import Path

from sqlalchemy import select

from app.db import init_db, session_scope
from app.fetcher import fetch_channel
from app.models import Filter, Subscription, TestPost, User
from app.own_channel import ensure_own_channel
from app.pipeline import match_filter

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_articles.json"

DEMO_USERS = {
    "Аня": [
        ("keyword", "санкции", None),
        ("keyword", "рубль", None),
        ("topic", "экономика ставка инфляция банки", 0.25),
    ],
    "Боря": [
        ("keyword", "сборная", None),
        ("topic", "искусственный интеллект нейросеть модель", 0.3),
    ],
}


def seed() -> None:
    init_db()
    with session_scope() as session:
        own_channel = ensure_own_channel(session)

        existing_titles = set(session.scalars(select(TestPost.title)))
        sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        for item in sample:
            if item["title"] not in existing_titles:
                session.add(TestPost(title=item["title"], body=item["body"]))
        session.flush()
        fetch_channel(session, own_channel)

        for name, filters in DEMO_USERS.items():
            if session.scalar(select(User.id).where(User.name == name)):
                continue
            user = User(name=name)
            session.add(user)
            session.flush()
            session.add(Subscription(user_id=user.id, channel_id=own_channel.id))
            for kind, query, threshold in filters:
                flt = Filter(user_id=user.id, kind=kind, query=query, threshold=threshold)
                session.add(flt)
                session.flush()
                match_filter(session, flt)

    print("Готово: демо-статьи и пользователи Аня, Боря. Откройте /docs → GET /api/users/{id}/feed")


if __name__ == "__main__":
    seed()
