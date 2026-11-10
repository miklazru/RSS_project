"""РОЛЬ 2 (Андреев) — сборщик RSS/Atom.

Сейчас ПРОСТАЯ ЗАГОТОВКА: скачивает ленту через httpx, разбирает feedparser'ом
(он понимает и RSS, и Atom), чистит HTML из описаний и передаёт записи в pipeline.
Что стоит доделать: условные запросы (ETag / If-Modified-Since), повторы при сбоях,
нормальная очистка HTML, ограничение размера ленты, проверка на 10–15 реальных лентах.

Важное правило, которое стоит сохранить: скачивание по сети идёт ВНЕ транзакции БД.
SQLite пишет по одному, и транзакция, ждущая медленный сайт, задержала бы всё приложение.
"""
import calendar
import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import session_scope
from app.models import Channel, TestPost, utcnow
from app.own_channel import items_from_posts
from app.pipeline import FeedItem, ingest

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")

# Некоторые сайты отдают 403 запросам без «браузерного» User-Agent
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; rss-news-filter/0.1)"}


# ---------- Разбор ленты (без сети и БД) ----------


def clean_html(text: str) -> str:
    """Убирает теги и HTML-сущности, схлопывает пробелы."""
    without_tags = _TAG.sub(" ", text or "")
    return _SPACES.sub(" ", html.unescape(without_tags)).strip()


def _entry_datetime(entry) -> Optional[datetime]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def _entry_guid(entry) -> str:
    guid = entry.get("id") or entry.get("link")
    if guid:
        return guid
    # у записи нет ни id, ни ссылки — делаем guid из содержимого
    raw = f"{entry.get('title', '')}|{entry.get('summary', '')}"
    return "hash-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def parse_feed(content: bytes) -> Tuple[Optional[str], List[FeedItem]]:
    """Разбирает RSS/Atom. Возвращает (название канала, записи)."""
    parsed = feedparser.parse(content)
    # feedparser не считает ошибкой обычную HTML-страницу: у неё просто нет записей и версии формата
    if not parsed.entries and not parsed.version:
        reason = parsed.get("bozo_exception") or "нет записей и не распознан формат"
        raise ValueError(f"не похоже на RSS/Atom: {reason}")

    title = clean_html(parsed.feed.get("title", "")) or None
    items = [
        FeedItem(
            guid=_entry_guid(entry),
            title=clean_html(entry.get("title", "")),
            summary=clean_html(entry.get("summary", "")),
            link=entry.get("link"),
            published_at=_entry_datetime(entry),
        )
        for entry in parsed.entries
    ]
    return title, items


# ---------- Скачивание (только сеть, без БД) ----------


@dataclass
class Download:
    """Итог скачивания: либо записи, либо текст ошибки."""

    title: Optional[str] = None
    items: List[FeedItem] = field(default_factory=list)
    error: Optional[str] = None


def download_feed(url: str, client: Optional[httpx.Client] = None) -> Download:
    """Скачивает и разбирает ленту. Ошибки не выбрасывает, а возвращает в Download.error."""
    http = client or httpx.Client(
        timeout=config.FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=_HEADERS,
    )
    try:
        response = http.get(url)
        response.raise_for_status()
        title, items = parse_feed(response.content)
        return Download(title=title, items=items)
    except Exception as error:
        log.warning("канал %s: %s", url, error)
        return Download(error=f"{type(error).__name__}: {error}"[:500])
    finally:
        if client is None:
            http.close()


# ---------- Запись в БД ----------


def _store(session: Session, channel: Channel, download: Download) -> int:
    """Сохраняет итог скачивания в канал и коммитит. Возвращает число новых статей."""
    new_count = 0
    error = download.error
    if error is None:
        try:
            # Точка сохранения: при ошибке откатятся только статьи этого канала,
            # а не чужие несохранённые изменения в той же сессии (например, новый пост).
            with session.begin_nested():
                new_count = len(ingest(session, channel, download.items))
        except Exception as ingest_error:
            log.exception("канал %s: ошибка записи статей", channel.url)
            error = f"{type(ingest_error).__name__}: {ingest_error}"[:500]

    if download.title and not channel.title:
        channel.title = download.title
    channel.last_error = error
    channel.last_fetched_at = utcnow()
    session.commit()
    return new_count


def _own_channel_download(session: Session) -> Download:
    """Наш тестовый канал читаем прямо из БД — пост появляется в лентах сразу."""
    posts = session.scalars(select(TestPost).order_by(TestPost.id))
    return Download(items=items_from_posts(posts))


def fetch_channel(
    session: Session, channel: Channel, client: Optional[httpx.Client] = None
) -> int:
    """Обновляет канал в уже открытой сессии. Возвращает число новых статей.

    Для внешних каналов из фона лучше fetch_channel_by_id: там сеть идёт вне транзакции.
    Эта функция удобна для тестового канала (сети нет) и для тестов.
    """
    if channel.is_test:
        download = _own_channel_download(session)
    else:
        download = download_feed(channel.url, client)
    return _store(session, channel, download)


def fetch_channel_by_id(channel_id: int, client: Optional[httpx.Client] = None) -> int:
    """Обновляет канал в три шага: короткое чтение -> сеть без БД -> короткая запись."""
    with session_scope() as session:
        channel = session.get(Channel, channel_id)
        if channel is None:
            return 0
        url = channel.url
        is_test = channel.is_test

    download = None if is_test else download_feed(url, client)

    with session_scope() as session:
        channel = session.get(Channel, channel_id)
        if channel is None:  # канал успели удалить, пока скачивали
            return 0
        if download is None:
            download = _own_channel_download(session)
        return _store(session, channel, download)


def fetch_all() -> int:
    """Один обход всех каналов. Ошибка одного канала не мешает остальным."""
    with session_scope() as session:
        channel_ids = list(session.scalars(select(Channel.id)))

    total_new = 0
    for channel_id in channel_ids:
        total_new += fetch_channel_by_id(channel_id)
    return total_new
