"""Сборщик: условные запросы (ETag), повторы, ограничения, обход каналов, миграция. Без интернета."""
import sqlite3

import httpx
from sqlalchemy import select

from app import config, db, fetcher
from app.db import session_scope
from app.fetcher import clean_html, download_feed, fetch_channel, parse_feed
from app.models import Article, Channel
from tests.test_fetcher import RSS, _channel_with_subscriber, mock_client


def recording_client(responses):
    """Клиент, который отдаёт ответы по очереди (последний повторяется) и запоминает запросы."""
    requests = []

    def handler(request):
        requests.append(request)
        item = responses[min(len(requests), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def feed_response(content=RSS, **headers):
    return httpx.Response(200, content=content, headers=headers)


# ---------- Условные запросы (ETag / If-Modified-Since) ----------


def test_download_sends_conditional_headers_and_handles_304():
    client, requests = recording_client([httpx.Response(304)])
    result = download_feed(
        "https://example.com/rss",
        client,
        etag='"abc"',
        last_modified="Mon, 10 Nov 2026 10:00:00 GMT",
    )

    assert result.not_modified is True
    assert result.error is None
    assert requests[0].headers["if-none-match"] == '"abc"'
    assert requests[0].headers["if-modified-since"] == "Mon, 10 Nov 2026 10:00:00 GMT"


def test_no_conditional_headers_on_first_fetch():
    client, requests = recording_client([feed_response()])
    download_feed("https://example.com/rss", client)
    assert "if-none-match" not in requests[0].headers
    assert "if-modified-since" not in requests[0].headers


def test_etag_is_stored_and_reused_and_304_keeps_articles(client):
    http, requests = recording_client(
        [
            feed_response(etag='"v1"', **{"last-modified": "Mon, 10 Nov 2026 10:00:00 GMT"}),
            httpx.Response(304),
        ]
    )
    with session_scope() as session:
        channel = _channel_with_subscriber(session, "https://example.com/rss")
        assert fetch_channel(session, channel, client=http) == 2
        assert channel.etag == '"v1"'
        assert channel.last_modified == "Mon, 10 Nov 2026 10:00:00 GMT"

        assert fetch_channel(session, channel, client=http) == 0
        assert requests[1].headers["if-none-match"] == '"v1"'
        assert channel.last_error is None
        assert channel.etag == '"v1"'  # 304 не стирает сохранённые заголовки
        assert len(list(session.scalars(select(Article)))) == 2


def test_failed_fetch_keeps_old_etag(client):
    http, _ = recording_client([feed_response(etag='"v1"'), httpx.Response(404)])
    with session_scope() as session:
        channel = _channel_with_subscriber(session, "https://example.com/rss")
        fetch_channel(session, channel, client=http)
        fetch_channel(session, channel, client=http)
        assert channel.last_error is not None
        assert channel.etag == '"v1"'


# ---------- Повторы ----------


def test_retries_5xx_then_succeeds():
    client, requests = recording_client([httpx.Response(503), httpx.Response(502), feed_response()])
    result = download_feed("https://example.com/rss", client)
    assert result.error is None
    assert len(result.items) == 2
    assert len(requests) == 3


def test_gives_up_after_retries_and_reports_last_status():
    client, requests = recording_client([httpx.Response(500)])
    result = download_feed("https://example.com/rss", client)
    assert "500" in result.error
    assert len(requests) == config.FETCH_RETRIES + 1


def test_does_not_retry_404():
    client, requests = recording_client([httpx.Response(404)])
    result = download_feed("https://example.com/rss", client)
    assert "404" in result.error
    assert len(requests) == 1


def test_retries_network_errors():
    client, requests = recording_client([httpx.ConnectTimeout("handshake timed out"), feed_response()])
    result = download_feed("https://example.com/rss", client)
    assert result.error is None
    assert len(requests) == 2


def test_network_error_after_all_retries_is_reported():
    client, requests = recording_client([httpx.ConnectTimeout("handshake timed out")])
    result = download_feed("https://example.com/rss", client)
    assert "ConnectTimeout" in result.error
    assert len(requests) == config.FETCH_RETRIES + 1


def test_backoff_grows_and_retry_after_is_respected(monkeypatch):
    waits = []
    monkeypatch.setattr(fetcher, "_sleep", waits.append)
    monkeypatch.setattr(config, "FETCH_BACKOFF_SECONDS", 1.0)

    client, _ = recording_client([httpx.Response(503), httpx.Response(503), feed_response()])
    download_feed("https://example.com/rss", client)
    assert waits == [1.0, 2.0]

    waits.clear()
    client, _ = recording_client([httpx.Response(429, headers={"retry-after": "7"}), feed_response()])
    download_feed("https://example.com/rss", client)
    assert waits == [7]


# ---------- Ограничения и очистка ----------


def test_oversized_feed_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "FETCH_MAX_BYTES", 100)
    result = download_feed("https://example.com/rss", mock_client(RSS))
    assert "лимита" in result.error


def test_oversized_content_length_is_rejected_before_reading(monkeypatch):
    monkeypatch.setattr(config, "FETCH_MAX_BYTES", 100)
    client, _ = recording_client([feed_response(**{"content-length": "999999"})])
    result = download_feed("https://example.com/rss", client)
    assert "лимита" in result.error


def test_entries_are_capped(monkeypatch):
    items = "".join(f"<item><title>Новость {i}</title><guid>g{i}</guid></item>" for i in range(30))
    big = f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{items}</channel></rss>'.encode()
    monkeypatch.setattr(config, "FETCH_MAX_ENTRIES", 10)
    _, parsed = parse_feed(big)
    assert [item.title for item in parsed] == [f"Новость {i}" for i in range(10)]


def test_clean_html_drops_scripts_styles_and_comments():
    dirty = "<p>Текст</p><script>alert('x')</script><style>p{color:red}</style><!-- скрыто --><b>ещё</b>"
    assert clean_html(dirty) == "Текст ещё"


# ---------- Обход каналов и миграция ----------


def test_fetch_all_survives_a_channel_that_blows_up(client, monkeypatch):
    with session_scope() as session:
        session.add_all([Channel(url="https://example.com/a"), Channel(url="https://example.com/b")])

    with session_scope() as session:
        broken_id = session.scalar(select(Channel.id).where(Channel.url == "https://example.com/a"))
        total_channels = len(list(session.scalars(select(Channel.id))))  # вместе с нашим тестовым каналом

    def fake_fetch(channel_id, client=None):
        if channel_id == broken_id:
            raise RuntimeError("boom")
        return 1

    monkeypatch.setattr(fetcher, "fetch_channel_by_id", fake_fetch)
    # сломанный канал не мешает остальным: каждый из остальных вернул по одной новой статье
    assert fetcher.fetch_all() == total_channels - 1


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE channels (id INTEGER PRIMARY KEY, url VARCHAR(1000) UNIQUE, title VARCHAR(500),"
        " is_test BOOLEAN, last_fetched_at DATETIME, last_error TEXT, created_at DATETIME)"
    )
    old.execute("INSERT INTO channels (url, is_test) VALUES ('https://old.example/rss', 0)")
    old.commit()
    old.close()

    db.configure(path)
    db.init_db()
    db.init_db()  # повторный запуск ничего не ломает

    with session_scope() as session:
        channel = session.scalar(select(Channel))
        assert channel.url == "https://old.example/rss"
        assert channel.etag is None and channel.last_modified is None
