"""РОЛЬ 2 (Андреев) — наш тестовый RSS-канал.

Сейчас РАБОЧАЯ ЗАГОТОВКА: посты хранятся в таблице TestPost и отдаются
как RSS 2.0 по адресу /rss/test.xml. Канал заведён в БД как обычный канал
с флагом is_test, поэтому на него можно подписаться, как на любой другой.

Сборщик берёт статьи этого канала прямо из БД, а не по HTTP: так новый пост
попадает в ленты сразу после публикации, без ожидания следующего обхода.
"""
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Iterable, List
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import Channel, TestPost
from app.pipeline import FeedItem

OWN_CHANNEL_TITLE = "Тестовый канал"


def own_channel_url() -> str:
    return f"{config.PUBLIC_BASE_URL}/rss/test.xml"


def ensure_own_channel(session: Session) -> Channel:
    """Создаёт тестовый канал при первом запуске. Если адрес сервиса поменялся — обновляет ссылку."""
    channel = session.scalar(select(Channel).where(Channel.is_test.is_(True)))
    if channel is None:
        channel = Channel(url=own_channel_url(), title=OWN_CHANNEL_TITLE, is_test=True)
        session.add(channel)
    elif channel.url != own_channel_url():
        channel.url = own_channel_url()
    session.flush()
    return channel


def _as_utc(value: datetime) -> datetime:
    # SQLite отдаёт даты без пояса; пишем мы их всегда в UTC
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def post_guid(post: TestPost) -> str:
    return f"test-post-{post.id}"


def post_link(post: TestPost) -> str:
    return f"{config.PUBLIC_BASE_URL}/rss/test/{post.id}"


def items_from_posts(posts: Iterable[TestPost]) -> List[FeedItem]:
    """Посты тестового канала в формате, который понимает pipeline."""
    return [
        FeedItem(
            guid=post_guid(post),
            title=post.title,
            summary=post.body,
            link=post_link(post),
            published_at=post.created_at,
        )
        for post in posts
    ]


def build_rss_xml(posts: Iterable[TestPost]) -> bytes:
    """RSS 2.0 для внешних читалок (и чтобы показать на демо, что канал настоящий)."""
    rss = ElementTree.Element("rss", version="2.0")
    channel = ElementTree.SubElement(rss, "channel")
    ElementTree.SubElement(channel, "title").text = OWN_CHANNEL_TITLE
    ElementTree.SubElement(channel, "link").text = own_channel_url()
    ElementTree.SubElement(channel, "description").text = "Посты, опубликованные из интерфейса"

    for post in posts:
        item = ElementTree.SubElement(channel, "item")
        ElementTree.SubElement(item, "title").text = post.title
        ElementTree.SubElement(item, "description").text = post.body
        ElementTree.SubElement(item, "link").text = post_link(post)
        ElementTree.SubElement(item, "guid", isPermaLink="false").text = post_guid(post)
        ElementTree.SubElement(item, "pubDate").text = format_datetime(_as_utc(post.created_at))

    return ElementTree.tostring(rss, encoding="utf-8", xml_declaration=True)
