"""Реестр движков: по виду фильтра (Filter.kind) выдаёт нужный движок.

Кроме обязательного match(), движок может иметь два НЕОБЯЗАТЕЛЬНЫХ метода:
    preload()                  — загрузить тяжёлое (модель) при старте сервиса;
    warm_up(articles)          — заранее посчитать тяжёлое для статей (эмбеддинги),
                                 чтобы потом match() работал быстро.
Ядро вызывает их ВНЕ транзакции БД: пока модель считает, база свободна для других запросов.
Если у движка этих методов нет, ничего не происходит.
"""
import logging
from typing import Dict, Iterable, List, Sequence

from app.matching.base import ArticleData, FilterData, Matcher, MatchResult
from app.matching.keyword import KeywordMatcher
from app.matching.semantic import TopicMatcher

log = logging.getLogger(__name__)

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


def preload_matchers() -> None:
    """Загружает модели при старте. Ошибку пишет в лог, но сервис не роняет."""
    for kind in FILTER_KINDS:
        preload = getattr(get_matcher(kind), "preload", None)
        if preload is None:
            continue
        try:
            preload()
            log.info("движок %s загружен", kind)
        except Exception:
            log.exception("движок %s не загрузился — фильтры этого вида не будут срабатывать", kind)


def warm_up_matchers(kinds: Iterable[str], articles: Sequence[ArticleData]) -> None:
    """Даёт движкам указанных видов заранее посчитать тяжёлое для статей.

    Вызывать ВНЕ транзакции БД. Ошибка прогрева не страшна: match() посчитает сам, только медленнее.
    """
    article_list: List[ArticleData] = list(articles)
    if not article_list:
        return
    for kind in sorted(set(kinds)):
        warm_up = getattr(get_matcher(kind), "warm_up", None)
        if warm_up is None:
            continue
        try:
            warm_up(article_list)
        except Exception:
            log.exception("прогрев движка %s не удался", kind)


__all__ = [
    "ArticleData",
    "FilterData",
    "Matcher",
    "MatchResult",
    "FILTER_KINDS",
    "get_matcher",
    "preload_matchers",
    "warm_up_matchers",
]
