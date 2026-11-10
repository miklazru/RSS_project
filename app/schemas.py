"""Форматы запросов и ответов API (то, что видит фронтенд)."""
from datetime import datetime, timezone
from typing import Annotated, List, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


def _assume_utc(value: datetime) -> datetime:
    # SQLite хранит даты без часового пояса, а пишем мы всегда в UTC.
    # Явно помечаем UTC, иначе фронтенд покажет время со сдвигом.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_assume_utc)]


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("имя не может быть пустым")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: UtcDatetime


class ChannelCreate(BaseModel):
    url: str = Field(min_length=1, max_length=1000)

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("ссылка должна начинаться с http:// или https://")
        return value


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    title: Optional[str]
    is_test: bool
    last_fetched_at: Optional[UtcDatetime]
    last_error: Optional[str]


class FilterCreate(BaseModel):
    kind: Literal["keyword", "topic"]
    query: str = Field(min_length=1, max_length=1000)
    # порог сходства для тем; если не задан — берётся порог по умолчанию
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("запрос не может быть пустым")
        return value


class FilterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    kind: str
    query: str
    threshold: Optional[float]
    created_at: UtcDatetime


class FeedMatchOut(BaseModel):
    """Почему статья в ленте: какой фильтр сработал и по какому критерию."""

    filter_id: int
    kind: str
    query: str
    score: float
    reasons: List[str]


class FeedEntryOut(BaseModel):
    article_id: int
    title: str
    summary: str
    link: Optional[str]
    published_at: Optional[UtcDatetime]
    fetched_at: UtcDatetime
    channel_id: int
    channel_title: str
    matches: List[FeedMatchOut]


class OwnPostCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=20000)


class OwnPostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    created_at: UtcDatetime
