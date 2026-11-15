"""РОЛЬ 1 (Матвей) — связка «статьи → фильтры → совпадения» и сборка ленты.

Три события запускают сопоставление:
  1. пришли новые статьи канала   -> проверяем их фильтрами всех подписчиков канала;
  2. пользователь добавил фильтр   -> проверяем им статьи всех его каналов;
  3. пользователь подписался на канал -> проверяем статьи канала всеми его фильтрами.

Лента пользователя = статьи из его каналов, которые прошли ХОТЯ БЫ ОДИН его фильтр
(фильтры объединяются через ИЛИ). У каждой статьи — список сработавших фильтров с причинами.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching import ArticleData, FilterData, get_matcher
from app.models import Article, Channel, Filter, Match, Subscription

log = logging.getLogger(__name__)


# ---------- Что сборщик отдаёт в pipeline ----------


@dataclass
class FeedItem:
    """Одна запись ленты, уже разобранная сборщиком (контракт сборщик -> pipeline)."""

    guid: str
    title: str
    summary: str = ""
    link: Optional[str] = None
    published_at: Optional[datetime] = None


# ---------- Преобразование строк БД в данные для движков ----------


def to_article_data(article: Article) -> ArticleData:
    return ArticleData(
        id=article.id,
        title=article.title or "",
        summary=article.summary or "",
        link=article.link,
        published_at=article.published_at,
    )


def to_filter_data(flt: Filter) -> FilterData:
    return FilterData(id=flt.id, kind=flt.kind, query=flt.query, threshold=flt.threshold)


# ---------- Сопоставление ----------


def _match_pair(
    session: Session, article: Article, flt: Filter, *, raise_on_match_error: bool = False
) -> bool:
    """Проверяет одну статью одним фильтром и сохраняет совпадение. Возвращает True, если совпало."""
    already_matched = session.scalar(
        select(Match.id).where(Match.article_id == article.id, Match.filter_id == flt.id)
    )
    if already_matched:
        return True

    matcher = get_matcher(flt.kind)
    try:
        result = matcher.match(to_article_data(article), to_filter_data(flt))
    except Exception:
        if raise_on_match_error:
            raise
        # ошибка в одном движке не должна ронять обработку всей ленты
        log.exception("движок %s упал на статье %s, фильтр %s", flt.kind, article.id, flt.id)
        return False

    if not result.matched:
        return False

    session.add(
        Match(
            article_id=article.id,
            filter_id=flt.id,
            user_id=flt.user_id,
            score=float(result.score),
            reasons_json=json.dumps(result.reasons, ensure_ascii=False),
        )
    )
    return True


def match_new_articles(session: Session, articles: List[Article]) -> int:
    """Событие 1: новые статьи -> фильтры всех подписчиков их каналов. Возвращает число совпадений."""
    matches_count = 0
    filters_by_channel: Dict[int, List[Filter]] = {}

    for article in articles:
        channel_id = article.channel_id
        if channel_id not in filters_by_channel:
            filters_by_channel[channel_id] = list(
                session.scalars(
                    select(Filter)
                    .join(Subscription, Subscription.user_id == Filter.user_id)
                    .where(Subscription.channel_id == channel_id)
                )
            )
        for flt in filters_by_channel[channel_id]:
            if _match_pair(session, article, flt):
                matches_count += 1

    session.flush()
    return matches_count


def match_filter(session: Session, flt: Filter, *, raise_on_match_error: bool = False) -> int:
    """Событие 2: новый фильтр -> статьи всех каналов его владельца."""
    articles = session.scalars(
        select(Article)
        .join(Subscription, Subscription.channel_id == Article.channel_id)
        .where(Subscription.user_id == flt.user_id)
    )
    matches_count = sum(
        1
        for article in articles
        if _match_pair(session, article, flt, raise_on_match_error=raise_on_match_error)
    )
    session.flush()
    return matches_count


def match_subscription(session: Session, user_id: int, channel_id: int) -> int:
    """Событие 3: подписка на канал -> статьи канала всеми фильтрами пользователя."""
    filters = list(session.scalars(select(Filter).where(Filter.user_id == user_id)))
    articles = session.scalars(select(Article).where(Article.channel_id == channel_id))
    matches_count = 0
    for article in articles:
        for flt in filters:
            if _match_pair(session, article, flt):
                matches_count += 1
    session.flush()
    return matches_count


# ---------- Данные для прогрева движков (читаются до тяжёлых вычислений) ----------


def user_filter_kinds(session: Session, user_id: int) -> Set[str]:
    """Какие виды фильтров есть у пользователя."""
    return set(session.scalars(select(Filter.kind).where(Filter.user_id == user_id).distinct()))


def subscriber_filter_kinds(session: Session, channel_id: int) -> Set[str]:
    """Какие виды фильтров есть у подписчиков канала."""
    return set(
        session.scalars(
            select(Filter.kind)
            .join(Subscription, Subscription.user_id == Filter.user_id)
            .where(Subscription.channel_id == channel_id)
            .distinct()
        )
    )


def articles_of_user(session: Session, user_id: int) -> List[ArticleData]:
    """Статьи всех каналов пользователя."""
    articles = session.scalars(
        select(Article)
        .join(Subscription, Subscription.channel_id == Article.channel_id)
        .where(Subscription.user_id == user_id)
    )
    return [to_article_data(article) for article in articles]


def articles_of_channel(session: Session, channel_id: int) -> List[ArticleData]:
    articles = session.scalars(select(Article).where(Article.channel_id == channel_id))
    return [to_article_data(article) for article in articles]


def item_to_article_data(item: FeedItem) -> ArticleData:
    """Запись ленты, которой ещё нет в базе (id = 0), — в том же виде, что и to_article_data."""
    return ArticleData(
        id=0,
        title=item.title or "",
        summary=item.summary or "",
        link=item.link,
        published_at=item.published_at,
    )


# ---------- Приём статей от сборщика ----------


def ingest(session: Session, channel: Channel, items: Iterable[FeedItem]) -> List[Article]:
    """Сохраняет новые статьи канала (дубли по guid пропускает) и сразу прогоняет их по фильтрам.

    Возвращает список только что добавленных статей.
    """
    known_guids = set(
        session.scalars(select(Article.guid).where(Article.channel_id == channel.id))
    )
    new_articles = []
    for item in items:
        if not item.guid or item.guid in known_guids:
            continue
        known_guids.add(item.guid)
        article = Article(
            channel_id=channel.id,
            guid=item.guid,
            title=item.title or "",
            summary=item.summary or "",
            link=item.link,
            published_at=item.published_at,
        )
        session.add(article)
        new_articles.append(article)

    # flush присваивает статьям id — без них нельзя сохранить совпадения
    session.flush()
    if new_articles:
        match_new_articles(session, new_articles)
    return new_articles


# ---------- Лента пользователя ----------


@dataclass
class FeedMatch:
    filter_id: int
    kind: str
    query: str
    score: float
    reasons: List[str]


@dataclass
class FeedEntry:
    article: Article
    channel_title: str
    matches: List[FeedMatch]


def get_feed(session: Session, user_id: int, limit: int = 50) -> List[FeedEntry]:
    """Статьи из текущих каналов пользователя, прошедшие хотя бы один его фильтр. Новые сверху."""
    rows = session.execute(
        select(Match, Article, Filter, Channel)
        .join(Article, Article.id == Match.article_id)
        .join(Filter, Filter.id == Match.filter_id)
        .join(Channel, Channel.id == Article.channel_id)
        .join(
            Subscription,
            (Subscription.channel_id == Article.channel_id)
            & (Subscription.user_id == Match.user_id),
        )
        .where(Match.user_id == user_id)
    ).all()

    entries: Dict[int, FeedEntry] = {}
    for match, article, flt, channel in rows:
        entry = entries.get(article.id)
        if entry is None:
            entry = FeedEntry(
                article=article,
                channel_title=channel.title or channel.url,
                matches=[],
            )
            entries[article.id] = entry
        entry.matches.append(
            FeedMatch(
                filter_id=flt.id,
                kind=flt.kind,
                query=flt.query,
                score=match.score,
                reasons=json.loads(match.reasons_json),
            )
        )

    def sort_key(entry: FeedEntry):
        # у статьи может не быть даты публикации — тогда берём время, когда мы её скачали
        when = entry.article.published_at or entry.article.fetched_at
        return (_as_naive_utc(when), entry.article.id)

    ordered = sorted(entries.values(), key=sort_key, reverse=True)
    return ordered[:limit]


def _as_naive_utc(value: datetime) -> datetime:
    # SQLite возвращает даты без часового пояса; приводим всё к одному виду, чтобы сравнивать
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value
