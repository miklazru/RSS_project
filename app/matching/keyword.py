"""РОЛЬ 3 (Есенин) — поиск по ключевым словам.

Сейчас НАИВНАЯ ЗАГЛУШКА: ищет запрос как подстроку без учёта регистра,
только целыми словами. Не умеет: словоформы («санкции» -> «санкциями»),
опечатки, фразы с другим порядком слов.

Заменить своей реализацией, сохранив класс KeywordMatcher и метод match().
"""
import re

from app.matching.base import ArticleData, FilterData, MatchResult


def _find_whole_word(needle: str, haystack: str):
    """Ищет needle в haystack целым словом без учёта регистра. Возвращает match или None."""
    pattern = r"(?<!\w)" + re.escape(needle.strip()) + r"(?!\w)"
    return re.search(pattern, haystack, flags=re.IGNORECASE)


class KeywordMatcher:
    kind = "keyword"

    def match(self, article: ArticleData, flt: FilterData) -> MatchResult:
        query = flt.query.strip()
        if not query:
            return MatchResult(matched=False)

        reasons = []
        found_in_title = _find_whole_word(query, article.title)
        if found_in_title:
            reasons.append(f"слово «{query}» в заголовке: «{found_in_title.group(0)}»")

        found_in_summary = _find_whole_word(query, article.summary)
        if found_in_summary:
            reasons.append(f"слово «{query}» в тексте: «{found_in_summary.group(0)}»")

        if not reasons:
            return MatchResult(matched=False)
        return MatchResult(matched=True, score=1.0, reasons=reasons)
