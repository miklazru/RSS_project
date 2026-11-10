"""Модель данных (таблицы БД).

Связи:
    User 1—N Filter          у пользователя свои фильтры
    User N—M Channel         через Subscription: какие каналы читает пользователь
    Channel 1—N Article      статьи канала
    Article + Filter -> Match  совпадение с причинами (почему статья в ленте пользователя)
    TestPost                 посты нашего тестового RSS-канала
"""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    filters: Mapped[List["Filter"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # наш собственный тестовый канал: его статьи берутся из таблицы TestPost, а не по HTTP
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # текст последней ошибки скачивания — показываем в UI, чтобы было видно, что канал «сломан»
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    articles: Mapped[List["Article"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))

    user: Mapped[User] = relationship(back_populates="subscriptions")
    channel: Mapped[Channel] = relationship(back_populates="subscriptions")


class Filter(Base):
    __tablename__ = "filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # "keyword" — ключевые слова, "topic" — тема (семантический поиск)
    kind: Mapped[str] = mapped_column(String(20))
    # для keyword: слово или фраза; для topic: описание темы
    query: Mapped[str] = mapped_column(Text)
    # порог сходства для topic; для keyword не используется
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="filters")


class Article(Base):
    __tablename__ = "articles"
    # одна и та же статья канала не должна сохраниться дважды
    __table_args__ = (UniqueConstraint("channel_id", "guid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    guid: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channel: Mapped[Channel] = relationship(back_populates="articles")


class Match(Base):
    __tablename__ = "matches"
    # статья + фильтр — не больше одного совпадения
    __table_args__ = (UniqueConstraint("article_id", "filter_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    filter_id: Mapped[int] = mapped_column(ForeignKey("filters.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    # список причин, сохранённый как JSON-строка
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    article: Mapped[Article] = relationship()
    filter: Mapped[Filter] = relationship()


class TestPost(Base):
    __tablename__ = "test_posts"
    # pytest не должен принимать этот класс за набор тестов
    __test__ = False

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
