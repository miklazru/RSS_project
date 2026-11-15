"""Прогрев движков вне транзакции БД.

Тяжёлые вычисления (эмбеддинги) не должны идти, пока открыта транзакция:
иначе база занята, и параллельные запросы и сборщик ждут или падают с "database is locked".

Фейковый тематический движок внутри warm_up() сам пробует взять блокировку на запись.
Если в этот момент открыта транзакция запроса, у него не получится — тест это поймает.
"""
import sqlite3

import httpx
import pytest

from app import matching
from app.fetcher import fetch_channel_by_id
from app.main import create_app
from app.matching.base import MatchResult


class FakeTopicMatcher:
    """Тема совпадает, если её текст есть в статье. «Эмбеддинги» — это просто кэш по тексту."""

    kind = "topic"

    def __init__(self, db_path):
        self.db_path = db_path
        self.cache = set()
        self.warm_up_calls = []
        self.database_was_free = []
        self.computed_inside_match = 0
        self.preloaded = False

    def preload(self):
        self.preloaded = True

    def _database_is_free(self) -> bool:
        connection = sqlite3.connect(self.db_path, timeout=0.2)
        try:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            connection.close()

    def warm_up(self, articles):
        self.database_was_free.append(self._database_is_free())
        self.warm_up_calls.append([article.title for article in articles])
        for article in articles:
            self.cache.add(article.text)

    def match(self, article, flt):
        if article.text not in self.cache:
            # «дорогое» вычисление внутри транзакции — то, чего прогрев должен избегать
            self.computed_inside_match += 1
            self.cache.add(article.text)
        if flt.query.lower() in article.text.lower():
            return MatchResult(matched=True, score=0.9, reasons=[f"тема «{flt.query}»: фейк"])
        return MatchResult(matched=False, score=0.1)


@pytest.fixture
def fake_topic(db_path, monkeypatch):
    fake = FakeTopicMatcher(db_path)
    monkeypatch.setitem(matching._matchers, "topic", fake)
    return fake


def test_new_topic_filter_warms_up_with_free_database(
    fake_topic, make_user, add_filter, post, feed
):
    user = make_user()
    post("Запуск ракеты к Луне")
    post("Пекарня открылась")
    fake_topic.warm_up_calls.clear()
    fake_topic.database_was_free.clear()
    fake_topic.computed_inside_match = 0

    add_filter(user["id"], "topic", "ракет")

    assert fake_topic.database_was_free == [True]
    assert sorted(fake_topic.warm_up_calls[0]) == ["Запуск ракеты к Луне", "Пекарня открылась"]
    # всё посчитано заранее — внутри транзакции ничего тяжёлого
    assert fake_topic.computed_inside_match == 0
    assert [item["title"] for item in feed(user["id"])] == ["Запуск ракеты к Луне"]


def test_own_post_is_warmed_up_before_transaction(fake_topic, make_user, add_filter, post, feed):
    user = make_user()
    add_filter(user["id"], "topic", "ракет")
    fake_topic.database_was_free.clear()
    fake_topic.computed_inside_match = 0

    post("Новая ракета", "Испытания прошли успешно")

    assert fake_topic.database_was_free == [True]
    assert fake_topic.computed_inside_match == 0
    assert [item["title"] for item in feed(user["id"])] == ["Новая ракета"]


def test_subscribe_warms_up_channel_articles(fake_topic, client, make_user, add_filter, post, feed):
    author = make_user("Автор")
    post("Ракета стартовала")
    reader = make_user("Читатель")
    add_filter(reader["id"], "topic", "ракет")
    own_channel_id = client.get(f"/api/users/{author['id']}/channels").json()[0]["id"]
    client.delete(f"/api/users/{reader['id']}/channels/{own_channel_id}")
    fake_topic.database_was_free.clear()
    fake_topic.computed_inside_match = 0

    response = client.put(f"/api/users/{reader['id']}/channels/{own_channel_id}")

    assert response.status_code == 204
    assert fake_topic.database_was_free == [True]
    assert fake_topic.computed_inside_match == 0
    assert [item["title"] for item in feed(reader["id"])] == ["Ракета стартовала"]


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Космос</title>
<item><title>Ракета вышла на орбиту</title><guid>a1</guid></item>
<item><title>Погода на выходные</title><guid>a2</guid></item>
</channel></rss>""".encode("utf-8")


def test_fetcher_warms_up_only_new_items_outside_transaction(
    fake_topic, client, make_user, add_filter, feed, monkeypatch
):
    monkeypatch.setattr("app.api.fetch_channel_by_id", lambda channel_id: 0)
    user = make_user()
    add_filter(user["id"], "topic", "ракет")
    channel = client.post(
        f"/api/users/{user['id']}/channels", json={"url": "https://example.com/rss"}
    ).json()
    mock = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=RSS)))
    fake_topic.warm_up_calls.clear()
    fake_topic.database_was_free.clear()
    fake_topic.computed_inside_match = 0

    fetch_channel_by_id(channel["id"], client=mock)

    assert fake_topic.database_was_free == [True]
    assert sorted(fake_topic.warm_up_calls[0]) == ["Погода на выходные", "Ракета вышла на орбиту"]
    assert fake_topic.computed_inside_match == 0
    assert [item["title"] for item in feed(user["id"])] == ["Ракета вышла на орбиту"]

    # повторный обход: старые статьи заново не прогреваются
    fake_topic.warm_up_calls.clear()
    fetch_channel_by_id(channel["id"], client=mock)
    assert fake_topic.warm_up_calls == []


def test_no_warm_up_without_topic_filters(fake_topic, make_user, add_filter, post):
    user = make_user()
    add_filter(user["id"], "keyword", "ракета")
    fake_topic.warm_up_calls.clear()

    # пост в свой канал прогревается всегда (это один текст), а подписка без тем — нет
    post("Ракета")
    assert len(fake_topic.warm_up_calls) == 1


def test_preload_is_called_at_startup(fake_topic, db_path):
    from fastapi.testclient import TestClient

    with TestClient(create_app(start_scheduler=False)):
        assert fake_topic.preloaded


def test_failing_preload_and_warm_up_do_not_break_service(db_path, monkeypatch, make_user):
    class BrokenMatcher:
        kind = "topic"

        def preload(self):
            raise RuntimeError("модель не найдена")

        def warm_up(self, articles):
            raise RuntimeError("модель не найдена")

        def match(self, article, flt):
            return MatchResult(matched=False)

    monkeypatch.setitem(matching._matchers, "topic", BrokenMatcher())
    from fastapi.testclient import TestClient

    with TestClient(create_app(start_scheduler=False)) as client:
        user = client.post("/api/users", json={"name": "Аня"}).json()
        response = client.post(
            f"/api/users/{user['id']}/filters", json={"kind": "topic", "query": "космос"}
        )
        assert response.status_code == 201
        assert client.post("/api/own-channel/posts", json={"title": "пост"}).status_code == 201
