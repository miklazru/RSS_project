"""КОНТРАКТ движков фильтрации. Менять только по договорённости всей команды.

Каждый движок (ключевые слова, темы) — это класс с методом match(article, flt).
Он не знает про БД, HTTP и UI: получает данные статьи и фильтра, возвращает результат.
Поэтому движки можно писать и тестировать отдельно от остального приложения.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class ArticleData:
    """Статья в том виде, в каком её видят движки."""

    id: int
    title: str
    summary: str
    link: Optional[str] = None
    published_at: Optional[datetime] = None

    @property
    def text(self) -> str:
        """Заголовок и описание одной строкой — удобно для поиска по всему тексту."""
        return f"{self.title}\n{self.summary}"


@dataclass(frozen=True)
class FilterData:
    """Фильтр пользователя."""

    id: int
    kind: str  # "keyword" или "topic"
    query: str  # слово / фраза / описание темы
    threshold: Optional[float] = None  # порог сходства для тем


@dataclass
class MatchResult:
    """Результат проверки статьи фильтром.

    matched — прошла ли статья фильтр;
    score   — сила совпадения: для ключевых слов обычно 1.0, для тем — косинусное сходство;
    reasons — причины для показа в ленте, по-человечески. Например:
              «слово "санкции" в заголовке (форма "санкциями")»,
              «тема "экономика": сходство 0.71, порог 0.60».
    """

    matched: bool
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)


class Matcher(Protocol):
    """Интерфейс движка. kind совпадает с Filter.kind, для которого движок работает."""

    kind: str

    def match(self, article: ArticleData, flt: FilterData) -> MatchResult:
        ...
