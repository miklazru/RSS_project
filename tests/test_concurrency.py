"""Параллельная работа: фоновый сборщик + запросы пользователей одновременно.

Живой запуск поймал "database is locked", когда сборщик и скачивание нового канала
писали в базу одновременно. Этот тест воспроизводит ту же ситуацию.
"""
import threading

from sqlalchemy import func, select

from app.db import session_scope
from app.fetcher import fetch_all
from app.models import Article, TestPost

THREADS = 6
ROUNDS = 10


def test_parallel_posts_fetches_and_filters(client, make_user):
    user = make_user("Аня")
    errors = []

    def publish_posts(thread_number):
        for round_number in range(ROUNDS):
            title = f"санкции {thread_number}-{round_number}"
            response = client.post("/api/own-channel/posts", json={"title": title})
            if response.status_code != 201:
                errors.append(response.text)

    def run_fetcher():
        for _ in range(ROUNDS):
            try:
                fetch_all()
            except Exception as error:
                errors.append(repr(error))

    def add_filters():
        for round_number in range(ROUNDS):
            response = client.post(
                f"/api/users/{user['id']}/filters",
                json={"kind": "keyword", "query": "санкции"},
            )
            if response.status_code != 201:
                errors.append(response.text)

    threads = [threading.Thread(target=publish_posts, args=(n,)) for n in range(THREADS)]
    threads.append(threading.Thread(target=run_fetcher))
    threads.append(threading.Thread(target=add_filters))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with session_scope() as session:
        posts_count = session.scalar(select(func.count(TestPost.id)))
        articles_count = session.scalar(select(func.count(Article.id)))
    # каждый пост превратился ровно в одну статью: без потерь и без дублей
    assert posts_count == THREADS * ROUNDS
    assert articles_count == posts_count

    feed = client.get(f"/api/users/{user['id']}/feed?limit=500").json()
    assert len(feed) == posts_count
