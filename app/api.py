"""HTTP API. Полная документация с кнопкой «Try it out» — на /docs."""
import html
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config, pipeline
from app.db import get_session
from app.fetcher import fetch_channel, fetch_channel_by_id
from app.matching import FILTER_KINDS, ArticleData, warm_up_matchers
from app.models import Channel, Filter, Match, Subscription, TestPost, User
from app.own_channel import build_rss_xml, ensure_own_channel
from app.schemas import (
    ChannelCreate,
    ChannelOut,
    FeedEntryOut,
    FeedMatchOut,
    FilterCreate,
    FilterOut,
    FilterUpdate,
    OwnPostCreate,
    OwnPostOut,
    UserCreate,
    UserOut,
    _assume_utc,
)

router = APIRouter(prefix="/api")
rss_router = APIRouter()


# ---------- вспомогательное ----------


def _get_user_or_404(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="пользователь не найден")
    return user


def _get_channel_or_404(session: Session, channel_id: int) -> Channel:
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="канал не найден")
    return channel


def _find_or_create_channel(session: Session, url: str) -> tuple[Channel, bool]:
    """Каналы общие для всех пользователей: одну ленту не храним дважды."""
    channel = session.scalar(select(Channel).where(Channel.url == url))
    if channel is not None:
        return channel, False
    channel = Channel(url=url)
    session.add(channel)
    session.flush()
    return channel, True


def _warm_up_before_subscribe(session: Session, user_id: int, channel_id: int) -> None:
    """Перед подпиской: прогреть движки на статьях канала, отпустив базу на время вычислений."""
    kinds = pipeline.user_filter_kinds(session, user_id)
    articles = pipeline.articles_of_channel(session, channel_id) if kinds else []
    # закрываем транзакцию запроса: пока модель считает, другие запросы могут писать в базу
    session.commit()
    warm_up_matchers(kinds, articles)


def _subscribe(session: Session, user_id: int, channel_id: int) -> None:
    exists = session.scalar(
        select(Subscription.id).where(
            Subscription.user_id == user_id, Subscription.channel_id == channel_id
        )
    )
    if exists:
        return
    session.add(Subscription(user_id=user_id, channel_id=channel_id))
    session.flush()
    # уже скачанные статьи канала сразу проверяем фильтрами пользователя
    pipeline.match_subscription(session, user_id, channel_id)


# ---------- пользователи ----------


@router.get("/users", response_model=List[UserOut], tags=["users"])
def list_users(session: Session = Depends(get_session)):
    return list(session.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserOut, status_code=201, tags=["users"])
def create_user(data: UserCreate, session: Session = Depends(get_session)):
    user = User(name=data.name)
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="пользователь с таким именем уже есть")
    # новый пользователь сразу подписан на наш тестовый канал — так проще показывать демо
    own_channel = ensure_own_channel(session)
    _subscribe(session, user.id, own_channel.id)
    session.commit()
    return user


@router.delete("/users/{user_id}", status_code=204, tags=["users"])
def delete_user(user_id: int, session: Session = Depends(get_session)):
    user = _get_user_or_404(session, user_id)
    session.delete(user)
    session.commit()
    return Response(status_code=204)


# ---------- каналы (общий список) ----------


@router.get("/channels", response_model=List[ChannelOut], tags=["channels"])
def list_channels(session: Session = Depends(get_session)):
    return list(session.scalars(select(Channel).order_by(Channel.id)))


@router.post("/channels", response_model=ChannelOut, tags=["channels"])
def add_channel(
    data: ChannelCreate,
    background: BackgroundTasks,
    response: Response,
    session: Session = Depends(get_session),
):
    channel, created = _find_or_create_channel(session, data.url)
    session.commit()
    response.status_code = 201 if created else 200
    if created:
        # скачиваем сразу, не дожидаясь планового обхода
        background.add_task(fetch_channel_by_id, channel.id)
    return channel


@router.post("/channels/{channel_id}/refresh", response_model=ChannelOut, tags=["channels"])
def refresh_channel(channel_id: int, session: Session = Depends(get_session)):
    """Скачать канал прямо сейчас (удобно на демо)."""
    _get_channel_or_404(session, channel_id)
    # закрываем транзакцию запроса до похода в сеть, иначе база будет занята на время скачивания
    session.commit()
    fetch_channel_by_id(channel_id)
    session.expire_all()
    return session.get(Channel, channel_id)


@router.delete("/channels/{channel_id}", status_code=204, tags=["channels"])
def delete_channel(channel_id: int, session: Session = Depends(get_session)):
    channel = _get_channel_or_404(session, channel_id)
    if channel.is_test:
        raise HTTPException(status_code=400, detail="тестовый канал удалить нельзя")
    session.delete(channel)
    session.commit()
    return Response(status_code=204)


# ---------- подписки пользователя ----------


@router.get("/users/{user_id}/channels", response_model=List[ChannelOut], tags=["subscriptions"])
def list_user_channels(user_id: int, session: Session = Depends(get_session)):
    _get_user_or_404(session, user_id)
    return list(
        session.scalars(
            select(Channel)
            .join(Subscription, Subscription.channel_id == Channel.id)
            .where(Subscription.user_id == user_id)
            .order_by(Channel.id)
        )
    )


@router.post(
    "/users/{user_id}/channels",
    response_model=ChannelOut,
    status_code=201,
    tags=["subscriptions"],
)
def add_and_subscribe(
    user_id: int,
    data: ChannelCreate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """Добавить канал по ссылке и сразу подписать на него пользователя (одним действием)."""
    _get_user_or_404(session, user_id)
    channel, created = _find_or_create_channel(session, data.url)
    session.commit()
    channel_id = channel.id
    if not created:
        # у нового канала статей ещё нет — прогревать нечего
        _warm_up_before_subscribe(session, user_id, channel_id)
    _subscribe(session, user_id, channel_id)
    session.commit()
    if created:
        background.add_task(fetch_channel_by_id, channel.id)
    return channel


@router.put("/users/{user_id}/channels/{channel_id}", status_code=204, tags=["subscriptions"])
def subscribe(user_id: int, channel_id: int, session: Session = Depends(get_session)):
    _get_user_or_404(session, user_id)
    _get_channel_or_404(session, channel_id)
    _warm_up_before_subscribe(session, user_id, channel_id)
    _subscribe(session, user_id, channel_id)
    session.commit()
    return Response(status_code=204)


@router.delete("/users/{user_id}/channels/{channel_id}", status_code=204, tags=["subscriptions"])
def unsubscribe(user_id: int, channel_id: int, session: Session = Depends(get_session)):
    subscription = session.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id, Subscription.channel_id == channel_id
        )
    )
    if subscription is None:
        raise HTTPException(status_code=404, detail="подписки нет")
    session.delete(subscription)
    session.commit()
    return Response(status_code=204)


# ---------- фильтры пользователя ----------


def _filter_out(session: Session, flt: Filter) -> FilterOut:
    out = FilterOut.model_validate(flt)
    if flt.kind == "topic":
        out.effective_threshold = (
            flt.threshold if flt.threshold is not None else config.DEFAULT_TOPIC_THRESHOLD
        )
    out.matches_count = session.scalar(
        select(func.count()).select_from(Match).where(Match.filter_id == flt.id)
    )
    return out


@router.get("/users/{user_id}/filters", response_model=List[FilterOut], tags=["filters"])
def list_filters(user_id: int, session: Session = Depends(get_session)):
    _get_user_or_404(session, user_id)
    filters = session.scalars(select(Filter).where(Filter.user_id == user_id).order_by(Filter.id))
    return [_filter_out(session, flt) for flt in filters]


@router.post(
    "/users/{user_id}/filters", response_model=FilterOut, status_code=201, tags=["filters"]
)
def create_filter(user_id: int, data: FilterCreate, session: Session = Depends(get_session)):
    _get_user_or_404(session, user_id)
    articles = pipeline.articles_of_user(session, user_id)
    # закрываем транзакцию запроса на время тяжёлых вычислений (эмбеддинги статей),
    # иначе база занята и другие запросы и сборщик ждут или падают с "database is locked"
    session.commit()
    warm_up_matchers([data.kind], articles)

    threshold = data.threshold if data.kind == "topic" else None
    flt = Filter(user_id=user_id, kind=data.kind, query=data.query, threshold=threshold)
    session.add(flt)
    session.flush()
    # новый фильтр сразу применяется к уже скачанным статьям
    pipeline.match_filter(session, flt)
    session.commit()
    return _filter_out(session, flt)


@router.patch("/users/{user_id}/filters/{filter_id}", response_model=FilterOut, tags=["filters"])
def update_filter(
    user_id: int, filter_id: int, data: FilterUpdate, session: Session = Depends(get_session)
):
    """Изменить порог сходства темы. Совпадения фильтра сразу пересчитываются."""
    flt = session.get(Filter, filter_id)
    if flt is None or flt.user_id != user_id:
        raise HTTPException(status_code=404, detail="фильтр не найден")
    if flt.kind != "topic":
        raise HTTPException(status_code=400, detail="порог сходства есть только у фильтров-тем")

    articles = pipeline.articles_of_user(session, user_id)
    # как при создании: модель считает вне транзакции, база в это время свободна
    session.commit()
    warm_up_matchers([flt.kind], articles)

    flt = session.get(Filter, filter_id)
    if flt is None:  # удалили, пока считали
        raise HTTPException(status_code=404, detail="фильтр не найден")
    flt.threshold = data.threshold
    session.execute(delete(Match).where(Match.filter_id == flt.id))
    session.flush()
    pipeline.match_filter(session, flt)
    session.commit()
    return _filter_out(session, flt)


@router.delete("/users/{user_id}/filters/{filter_id}", status_code=204, tags=["filters"])
def delete_filter(user_id: int, filter_id: int, session: Session = Depends(get_session)):
    flt = session.get(Filter, filter_id)
    if flt is None or flt.user_id != user_id:
        raise HTTPException(status_code=404, detail="фильтр не найден")
    session.delete(flt)
    session.commit()
    return Response(status_code=204)


# ---------- лента ----------


@router.get("/users/{user_id}/feed", response_model=List[FeedEntryOut], tags=["feed"])
def get_feed(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    """Отфильтрованная лента. У каждой статьи — сработавшие фильтры и причины."""
    _get_user_or_404(session, user_id)
    entries = pipeline.get_feed(session, user_id, limit=limit)
    return [
        FeedEntryOut(
            article_id=entry.article.id,
            title=entry.article.title,
            summary=entry.article.summary,
            link=entry.article.link,
            published_at=entry.article.published_at,
            fetched_at=entry.article.fetched_at,
            channel_id=entry.article.channel_id,
            channel_title=entry.channel_title,
            matches=[FeedMatchOut(**vars(match)) for match in entry.matches],
        )
        for entry in entries
    ]


# ---------- наш тестовый RSS-канал ----------


@router.get("/own-channel/posts", response_model=List[OwnPostOut], tags=["own channel"])
def list_own_posts(session: Session = Depends(get_session)):
    return list(session.scalars(select(TestPost).order_by(TestPost.id.desc())))


@router.post(
    "/own-channel/posts", response_model=OwnPostOut, status_code=201, tags=["own channel"]
)
def create_own_post(data: OwnPostCreate, session: Session = Depends(get_session)):
    """Опубликовать пост в наш RSS-канал. Он сразу попадает в ленты подписчиков."""
    title = data.title.strip()
    body = data.body.strip()
    # прогрев до первого обращения к базе: транзакция ещё не открыта
    warm_up_matchers(FILTER_KINDS, [ArticleData(id=0, title=title, summary=body)])

    post = TestPost(title=title, body=body)
    session.add(post)
    session.flush()
    channel = ensure_own_channel(session)
    fetch_channel(session, channel)
    session.commit()
    return post


@rss_router.get("/rss/test/{post_id}", response_class=HTMLResponse, tags=["own channel"])
def own_post_page(post_id: int, session: Session = Depends(get_session)):
    """Страница поста: на неё ведут ссылки из /rss/test.xml и из ленты."""
    post = session.get(TestPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="пост не найден")
    created = _assume_utc(post.created_at)
    title = html.escape(post.title)
    return HTMLResponse(
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title><link rel='stylesheet' href='/static/style.css'></head>"
        "<body><div class='sheet'><div class='paper'><main><article class='article'>"
        "<div class='meta'><span>Тестовый канал</span><span>·</span>"
        f"<time datetime='{created.isoformat()}'>{created:%d.%m.%Y %H:%M} UTC</time></div>"
        f"<h4>{title}</h4>"
        f"<p class='summary' style='white-space:pre-wrap'>{html.escape(post.body)}</p>"
        "</article><p class='small'><a href='/'>← к ленте</a></p></main></div></div></body></html>"
    )


@rss_router.get("/rss/test.xml", tags=["own channel"])
def own_channel_rss(session: Session = Depends(get_session)):
    posts = list(session.scalars(select(TestPost).order_by(TestPost.id.desc())))
    return Response(content=build_rss_xml(posts), media_type="application/rss+xml; charset=utf-8")
