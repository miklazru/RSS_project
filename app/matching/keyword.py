"""Поиск ключевых слов и фраз без зависимости от БД, сборщика и UI."""
import re
from collections import Counter
from functools import lru_cache
from typing import NamedTuple

import pymorphy3

from app.matching.base import ArticleData, FilterData, MatchResult


_TOKEN = re.compile(r"\w+(?:(?:\+\+|#)(?!\w))?")
_RUSSIAN = re.compile(r"[а-яё]+")
_LATIN = re.compile(r"[a-z]+")
_PHRASE_BREAK = re.compile(r"[.!?;\r\n]")
_MORPH = pymorphy3.MorphAnalyzer(lang="ru")


class _Word(NamedTuple):
    form: str
    lemma: str
    known: bool


def _normalize(text: str) -> str:
    return text.casefold().replace("ё", "е")


@lru_cache(maxsize=16_384)
def _word(form: str) -> _Word:
    if _RUSSIAN.fullmatch(form) and _MORPH.word_is_known(form):
        return _Word(form, _normalize(_MORPH.parse(form)[0].normal_form), True)
    return _Word(form, form, False)


@lru_cache(maxsize=4_096)
def _word_forms(word: _Word) -> frozenset[str]:
    if word.known:
        return frozenset(_normalize(parse.word) for parse in _MORPH.parse(word.form)[0].lexeme)
    return frozenset((word.form,))


def _one_edit(left: str, right: str) -> bool:
    """Ровно одна вставка, пропуск, замена или перестановка соседних букв."""
    if len(left) > len(right):
        left, right = right, left
    if len(right) < 4 or len(right) - len(left) > 1:
        return False

    for i, (first, second) in enumerate(zip(left, right)):
        if first != second:
            break
    else:
        return len(left) < len(right)

    if len(left) < len(right):
        return left[i:] == right[i + 1:]
    return left[i + 1:] == right[i + 1:] or (
        left[i:i + 2] == right[i:i + 2][::-1] and left[i + 2:] == right[i + 2:]
    )


def _is_typo(query: _Word, found: _Word) -> bool:
    if _RUSSIAN.fullmatch(query.form) and _RUSSIAN.fullmatch(found.form):
        # Правильно написанное русское слово не считаем опечаткой другого слова.
        if query.known and found.known:
            return False
        return any(
            _one_edit(expected, actual)
            for expected in _word_forms(query)
            for actual in _word_forms(found)
        )
    return bool(
        _LATIN.fullmatch(query.form)
        and _LATIN.fullmatch(found.form)
        and _one_edit(query.form, found.form)
    )


def _find_phrase(
    text: str, query: list[_Word], query_lemmas: Counter[str]
) -> tuple[str, bool] | None:
    """Находит окно слов и возвращает исходный фрагмент и признак опечатки."""
    size = len(query)
    # Между предложениями фразу не собираем. Запятые и дефисы разделяют слова.
    segments = _PHRASE_BREAK.split(text) if size > 1 else [text]
    typo = None
    for segment in segments:
        tokens = list(_TOKEN.finditer(segment))
        if len(tokens) < size:
            continue
        words = [_word(_normalize(token.group())) for token in tokens]
        for start in range(len(tokens) - size + 1):
            end = start + size
            window = words[start:end]
            # Counter сохраняет кратность слов при перестановке.
            found_lemmas = Counter(word.lemma for word in window)
            first, last = tokens[start].start(), tokens[end - 1].end()
            if found_lemmas == query_lemmas:
                return segment[first:last], False
            if typo is not None:
                continue

            missing = list((query_lemmas - found_lemmas).elements())
            extra = list((found_lemmas - query_lemmas).elements())
            if len(missing) == 1 and len(extra) == 1 and any(
                _is_typo(expected, found)
                for expected in query if expected.lemma == missing[0]
                for found in window if found.lemma == extra[0]
            ):
                typo = segment[first:last], True
    return typo


class KeywordMatcher:
    kind = "keyword"

    def match(self, article: ArticleData, flt: FilterData) -> MatchResult:
        query_text = flt.query.strip()
        query = [_word(_normalize(token)) for token in _TOKEN.findall(query_text)]
        if not query:
            return MatchResult(matched=False)

        lemmas = Counter(word.lemma for word in query)
        label = "слово" if len(query) == 1 else "фраза"
        reasons = []
        for text, location in ((article.title, "в заголовке"), (article.summary, "в тексте")):
            match = _find_phrase(text, query, lemmas)
            if match is not None:
                found, has_typo = match
                suffix = " (совпадение с опечаткой)" if has_typo else ""
                reasons.append(f"{label} «{query_text}» {location}: «{found}»{suffix}")

        if not reasons:
            return MatchResult(matched=False)
        return MatchResult(matched=True, score=1.0, reasons=reasons)
