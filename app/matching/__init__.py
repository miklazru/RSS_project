"""Реестр движков: по виду фильтра (Filter.kind) выдаёт нужный движок."""
from typing import Dict

from app.matching.base import ArticleData, FilterData, Matcher, MatchResult
from app.matching.keyword import KeywordMatcher
from app.matching.semantic import TopicMatcher

FILTER_KINDS = ("keyword", "topic")

# Движки создаются один раз на всё приложение (семантическому это важно: модель тяжёлая).
_matchers: Dict[str, Matcher] = {}


def get_matcher(kind: str) -> Matcher:
    if kind not in _matchers:
        if kind == "keyword":
            _matchers[kind] = KeywordMatcher()
        elif kind == "topic":
            _matchers[kind] = TopicMatcher()
        else:
            raise ValueError(f"неизвестный вид фильтра: {kind}")
    return _matchers[kind]


__all__ = [
    "ArticleData",
    "FilterData",
    "Matcher",
    "MatchResult",
    "FILTER_KINDS",
    "get_matcher",
]
