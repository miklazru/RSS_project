"""Сборщик: разбор RSS и Atom, защита от дублей, ошибки каналов. Без интернета — через MockTransport."""
import feedparser
import httpx
from sqlalchemy import select

from app.db import session_scope
from app.fetcher import clean_html, fetch_channel, parse_feed
from app.models import Article, Channel, Filter, Subscription, User

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Новости &amp; события</title>
  <item>
    <title>Новые санкции против банков</title>
    <description>&lt;p&gt;Текст &lt;b&gt;новости&lt;/b&gt;&lt;/p&gt;</description>
    <link>https://example.com/1</link>
    <guid>news-1</guid>
    <pubDate>Mon, 10 Nov 2026 10:00:00 +0300</pubDate>
  </item>
  <item>
    <title>Погода на выходные</title>
    <link>https://example.com/2</link>
  </item>
</channel></rss>""".encode("utf-8")

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom-лента</title>
  <entry>
    <title>Санкции в Atom</title>
    <id>urn:atom:1</id>
    <link href="https://example.com/atom/1"/>
    <updated>2026-11-10T07:00:00Z</updated>
    <summary>Кратко</summary>
  </entry>
</feed>""".encode("utf-8")

# Лента в старой русской кодировке — такие ещё встречаются
RSS_CP1251 = """<?xml version="1.0" encoding="windows-1251"?>
<rss version="2.0"><channel><title>Старая лента</title>
<item><title>Санкции в cp1251</title><guid>cp-1</guid></item>
</channel></rss>""".encode("cp1251")


def mock_client(content: bytes, status: int = 200) -> httpx.Client:
    def handler(request):
        return httpx.Response(status, content=content)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_clean_html():
    assert clean_html("<p>Текст <b>новости</b> &amp; ещё</p>") == "Текст новости & ещё"


def test_parse_rss():
    title, items = parse_feed(RSS)
    assert title == "Новости & события"
    assert [item.title for item in items] == ["Новые санкции против банков", "Погода на выходные"]
    assert items[0].guid == "news-1"
    assert items[0].summary == "Текст новости"
    assert items[0].published_at is not None
    # нет guid — берём ссылку
    assert items[1].guid == "https://example.com/2"


def test_parse_atom():
    title, items = parse_feed(ATOM)
    assert title == "Atom-лента"
    assert items[0].title == "Санкции в Atom"
    assert items[0].link == "https://example.com/atom/1"


def test_parse_cp1251():
    _, items = parse_feed(RSS_CP1251)
    assert items[0].title == "Санкции в cp1251"


def _channel_with_subscriber(session, url):
    user = User(name="Аня")
    channel = Channel(url=url)
    session.add_all([user, channel])
    session.flush()
    session.add(Subscription(user_id=user.id, channel_id=channel.id))
    session.add(Filter(user_id=user.id, kind="keyword", query="санкции"))
    session.flush()
    return channel


def test_fetch_saves_articles_once_and_matches(client):
    with session_scope() as session:
        channel = _channel_with_subscriber(session, "https://example.com/rss")
        first = fetch_channel(session, channel, client=mock_client(RSS))
        second = fetch_channel(session, channel, client=mock_client(RSS))
        assert first == 2
        assert second == 0  # дубли не сохраняются
        assert channel.title == "Новости & события"
        assert channel.last_error is None

    feed = client.get("/api/users/1/feed").json()
    assert [item["title"] for item in feed] == ["Новые санкции против банков"]


def test_broken_channel_records_error_and_keeps_working(client):
    with session_scope() as session:
        channel = _channel_with_subscriber(session, "https://example.com/broken")
        new_count = fetch_channel(session, channel, client=mock_client(b"", status=500))
        assert new_count == 0
        assert "500" in channel.last_error

        # канал «починился» — ошибка пропадает
        fetch_channel(session, channel, client=mock_client(RSS))
        assert channel.last_error is None
        assert len(list(session.scalars(select(Article)))) == 2


def test_not_a_feed_is_an_error(client):
    with session_scope() as session:
        channel = _channel_with_subscriber(session, "https://example.com/html")
        fetch_channel(session, channel, client=mock_client(b"<html><body>hi</body></html>"))
        assert channel.last_error is not None


def test_own_channel_rss_is_valid(client, post):
    post("Первый пост", "Текст первого")
    post("Второй пост", "Текст второго")

    response = client.get("/rss/test.xml")

    assert response.status_code == 200
    assert "rss" in response.headers["content-type"]
    parsed = feedparser.parse(response.content)
    assert parsed.bozo == 0
    assert [entry.title for entry in parsed.entries] == ["Второй пост", "Первый пост"]


def test_failed_fetch_keeps_other_unsaved_changes(client):
    """Ошибка канала откатывает только его статьи, а не чужие несохранённые изменения в сессии."""
    with session_scope() as session:
        channel = _channel_with_subscriber(session, "https://example.com/broken")
        # пользователь и канал ещё не закоммичены — ошибка сборщика не должна их стереть
        fetch_channel(session, channel, client=mock_client(b"", status=500))
        assert channel.last_error is not None

    with session_scope() as session:
        assert session.scalar(select(Channel).where(Channel.url == "https://example.com/broken"))
        assert session.scalar(select(User).where(User.name == "Аня"))


def test_valid_empty_feed_is_not_an_error(client):
    empty_rss = '<?xml version="1.0"?><rss version="2.0"><channel><title>Пусто</title></channel></rss>'.encode()
    with session_scope() as session:
        channel = _channel_with_subscriber(session, "https://example.com/empty")
        new_count = fetch_channel(session, channel, client=mock_client(empty_rss))
        assert new_count == 0
        assert channel.last_error is None
