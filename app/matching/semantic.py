"""РОЛЬ 4 (Конзафаров) — семантический поиск по темам.

Сейчас НАИВНАЯ ЗАГЛУШКА без модели: «сходство» — доля слов описания темы,
которые встречаются в статье (без учёта словоформ). Нужна только для того,
чтобы приложение работало целиком, пока нет настоящего движка.

Заменить реализацией на эмбеддингах, сохранив класс TopicMatcher и метод match().
Подсказки:
- модель загружать один раз (в __init__ или лениво), не на каждый вызов;
- эмбеддинги статей кэшировать по article.id;
- модель скачать заранее и класть в образ: на демо HuggingFace может быть недоступен.
"""
import re

from app import config
from app.matching.base import ArticleData, FilterData, MatchResult

_WORD = re.compile(r"\w+", flags=re.UNICODE)


def _words(text: str) -> set:
    # короткие слова (предлоги, союзы) выкидываем
    return {word.lower() for word in _WORD.findall(text) if len(word) > 3}


class TopicMatcher:
    kind = "topic"

    def match(self, article: ArticleData, flt: FilterData) -> MatchResult:
        topic_words = _words(flt.query)
        if not topic_words:
            return MatchResult(matched=False)

        common_words = topic_words & _words(article.text)
        similarity = len(common_words) / len(topic_words)
        threshold = flt.threshold if flt.threshold is not None else config.DEFAULT_TOPIC_THRESHOLD

        if similarity < threshold:
            return MatchResult(matched=False, score=similarity)

        reason = (
            f"тема «{flt.query}»: сходство {similarity:.2f}, порог {threshold:.2f} "
            f"(заглушка: общие слова {', '.join(sorted(common_words))})"
        )
        return MatchResult(matched=True, score=similarity, reasons=[reason])
