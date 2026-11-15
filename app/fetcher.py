"""РОЛЬ 2 (Андреев) — сборщик RSS/Atom.

Сейчас ПРОСТАЯ ЗАГОТОВКА: скачивает ленту через httpx, разбирает feedparser'ом
(он понимает и RSS, и Atom), чистит HTML из описаний и передаёт записи в pipeline.
Умеет: условные запросы (ETag / If-Modified-Since, ответ 304 — не ошибка), повторы с паузой
при сетевых сбоях, 429 и 5xx, ограничение размера ответа и числа записей, очистку HTML,
параллельный обход каналов. Проверка на реальных лентах: python -m scripts.check_feeds.

Важное правило, которое стоит сохранить: скачивание по сети идёт ВНЕ транзакции БД.
SQLite пишет по одному, и транзакция, ждущая медленный сайт, задержала бы всё приложение.
"""
import calendar
import hashlib
import html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import session_scope
from app.matching import warm_up_matchers
from app.models import Article, Channel, TestPost, utcnow
from app.own_channel import items_from_posts
from app.pipeline import FeedItem, ingest, item_to_article_data, subscriber_filter_kinds

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
# содержимое этих тегов — не текст новости, а код или разметка; HTML-комментарии тоже выбрасываем
_NON_TEXT = re.compile(r"<(script|style)\b.*?</\1\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL)
_SPACES = re.compile(r"\s+")

# Некоторые сайты отдают 403 запросам без «браузерного» User-Agent
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; rss-news-filter/0.1)"}

# На эти ответы имеет смысл повторить запрос: сервер перегружен или просит подождать
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRY_AFTER_SECONDS = 30

# отдельная функция, чтобы тесты могли не ждать по-настоящему
_sleep = time.sleep


# ---------- Разбор ленты (без сети и БД) ----------


def clean_html(text: str) -> str:
    """Убирает теги и HTML-сущности, схлопывает пробелы."""
    without_tags = _TAG.sub(" ", _NON_TEXT.sub(" ", text or ""))
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


def parse_feed(
    content: bytes, max_entries: Optional[int] = None
) -> Tuple[Optional[str], List[FeedItem]]:
    """Разбирает RSS/Atom. Возвращает (название канала, записи).

    Берёт не больше max_entries записей (по умолчанию FETCH_MAX_ENTRIES): в лентах новые идут первыми.
    """
    if max_entries is None:
        max_entries = config.FETCH_MAX_ENTRIES
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
        for entry in parsed.entries[:max_entries]
    ]
    return title, items


# ---------- Скачивание (только сеть, без БД) ----------


@dataclass
class Download:
    """Итог скачивания: записи, «не менялось» (304) или текст ошибки."""

    title: Optional[str] = None
    items: List[FeedItem] = field(default_factory=list)
    error: Optional[str] = None
    not_modified: bool = False
    etag: Optional[str] = None
    last_modified: Optional[str] = None


def _read_limited(response: httpx.Response) -> bytes:
    """Читает тело ответа, но не больше FETCH_MAX_BYTES — иначе огромная лента съест всю память."""
    limit = config.FETCH_MAX_BYTES
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ValueError(f"лента больше лимита {limit} байт (Content-Length {declared})")
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > limit:
            raise ValueError(f"лента больше лимита {limit} байт")
        chunks.append(chunk)
    return b"".join(chunks)


def _retry_delay(attempt: int, response: Optional[httpx.Response] = None) -> float:
    """Сколько ждать перед повтором: Retry-After от сервера, иначе 1 с, 2 с, 4 с... (FETCH_BACKOFF_SECONDS)."""
    if response is not None:
        retry_after = response.headers.get("retry-after", "")
        if retry_after.isdigit():
            return min(int(retry_after), _MAX_RETRY_AFTER_SECONDS)
    return config.FETCH_BACKOFF_SECONDS * (2**attempt)


def _download_once(
    http: httpx.Client, url: str, headers: dict, can_retry: bool
) -> Tuple[Optional[Download], Optional[httpx.Response]]:
    """Одна попытка. (Download, None) — готово; (None, ответ) — сервер просит повторить позже."""
    with http.stream("GET", url, headers=headers) as response:
        if response.status_code == 304:
            return Download(not_modified=True), None
        if can_retry and response.status_code in _RETRY_STATUSES:
            return None, response
        response.raise_for_status()
        title, items = parse_feed(_read_limited(response))
        return (
            Download(
                title=title,
                items=items,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            ),
            None,
        )


def download_feed(
    url: str,
    client: Optional[httpx.Client] = None,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> Download:
    """Скачивает и разбирает ленту. Ошибки не выбрасывает, а возвращает в Download.error.

    etag / last_modified прошлого скачивания превращаются в If-None-Match / If-Modified-Since:
    если лента не менялась, сервер отвечает 304 и мы получаем Download(not_modified=True).
    Сетевые сбои, 429 и 5xx повторяются FETCH_RETRIES раз с растущей паузой.
    """
    http = client or httpx.Client(
        timeout=config.FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=_HEADERS,
    )
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        attempts = config.FETCH_RETRIES + 1
        for attempt in range(attempts):
            can_retry = attempt < attempts - 1
            try:
                download, retry_response = _download_once(http, url, headers, can_retry)
            except httpx.TransportError as error:
                if not can_retry:
                    raise
                log.info("канал %s: %s, повтор %d", url, error, attempt + 1)
                _sleep(_retry_delay(attempt))
                continue
            if download is not None:
                return download
            log.info("канал %s: ответ %s, повтор %d", url, retry_response.status_code, attempt + 1)
            _sleep(_retry_delay(attempt, retry_response))
        raise RuntimeError("недостижимо")  # цикл всегда возвращает результат или бросает ошибку
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
    if error is None and not download.not_modified:
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
    if error is None and not download.not_modified:
        # запоминаем заголовки только после успешной записи, иначе при сбое пропустим статьи
        channel.etag = download.etag
        channel.last_modified = download.last_modified
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
        download = download_feed(channel.url, client, channel.etag, channel.last_modified)
    return _store(session, channel, download)


def fetch_channel_by_id(channel_id: int, client: Optional[httpx.Client] = None) -> int:
    """Обновляет канал: короткое чтение -> сеть и прогрев движков без БД -> короткая запись."""
    with session_scope() as session:
        channel = session.get(Channel, channel_id)
        if channel is None:
            return 0
        url, is_test = channel.url, channel.is_test
        etag, last_modified = channel.etag, channel.last_modified
        known_guids = set(session.scalars(select(Article.guid).where(Article.channel_id == channel_id)))
        filter_kinds = subscriber_filter_kinds(session, channel_id)

    download = None if is_test else download_feed(url, client, etag, last_modified)

    if download is not None and download.items and filter_kinds:
        # эмбеддинги новых статей считаем здесь, вне транзакции: внутри неё match() будет быстрым
        new_items = [item for item in download.items if item.guid not in known_guids]
        warm_up_matchers(filter_kinds, [item_to_article_data(item) for item in new_items])

    with session_scope() as session:
        channel = session.get(Channel, channel_id)
        if channel is None:  # канал успели удалить, пока скачивали
            return 0
        if download is None:
            download = _own_channel_download(session)
        return _store(session, channel, download)


def fetch_all() -> int:
    """Один обход всех каналов, FETCH_WORKERS каналов одновременно.

    Параллельно, чтобы несколько «мёртвых» сайтов с долгим таймаутом не растягивали обход
    дольше интервала. Ошибка одного канала не мешает остальным.
    """
    with session_scope() as session:
        channel_ids = list(session.scalars(select(Channel.id)))

    def fetch_one(channel_id: int) -> int:
        try:
            return fetch_channel_by_id(channel_id)
        except Exception:
            log.exception("канал id=%s: не удалось обновить", channel_id)
            return 0

    with ThreadPoolExecutor(max_workers=max(1, config.FETCH_WORKERS)) as pool:
        return sum(pool.map(fetch_one, channel_ids))
