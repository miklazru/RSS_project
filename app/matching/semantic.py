"""Семантическое сопоставление темы и статьи через локальную MiniLM."""

import os
from collections import OrderedDict
from pathlib import Path
from threading import RLock

import numpy as np

from app import config
from app.matching.base import ArticleData, FilterData, MatchResult


DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "models"
    / "multilingual-minilm"
)


class TopicMatcher:
    kind = "topic"

    def __init__(self, model=None, cache_size=2000):
        if cache_size < 1:
            raise ValueError("Размер кэша должен быть положительным")

        self._model = model
        self._cache_size = cache_size
        self._article_cache = OrderedDict()
        self._topic_cache = OrderedDict()
        self._lock = RLock()

    def _encode(self, text):
        # Загружаем модель один раз, при первом непустом запросе.
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            model_path = Path(
                os.environ.get(
                    "TOPIC_MODEL_PATH",
                    str(DEFAULT_MODEL_PATH),
                )
            )

            if not model_path.is_dir():
                raise RuntimeError(
                    f"Модель не найдена: {model_path}. "
                    "Выполните python -m scripts.download_minilm"
                )

            self._model = SentenceTransformer(
                str(model_path),
                device="cpu",
                local_files_only=True,
            )

        # MiniLM получает обычный текст без query: и passage:.
        vector = self._model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        vector = np.asarray(vector, dtype=np.float64)

        if not np.isfinite(vector).all():
            raise ValueError("Модель вернула некорректный эмбеддинг")

        return vector

    def _remember(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)

        # Удаляем запись, которая дольше всего не использовалась.
        if len(cache) > self._cache_size:
            cache.popitem(last=False)

    def _article_embedding(self, article_id, text):
        cached = self._article_cache.get(article_id)

        # Если текст изменился, старый эмбеддинг использовать нельзя.
        if cached is not None and cached[0] == text:
            self._article_cache.move_to_end(article_id)
            return cached[1]

        vector = self._encode(text)

        self._remember(
            self._article_cache,
            article_id,
            (text, vector),
        )
        return vector

    def _topic_embedding(self, query):
        # Ключ — текст темы, поэтому изменение фильтра обновит вектор.
        if query in self._topic_cache:
            self._topic_cache.move_to_end(query)
            return self._topic_cache[query]

        vector = self._encode(query)
        self._remember(self._topic_cache, query, vector)
        return vector

    def match(
        self,
        article: ArticleData,
        flt: FilterData,
    ) -> MatchResult:
        query = flt.query.strip()

        text = "\n".join(
            part.strip()
            for part in (article.title, article.summary)
            if part and part.strip()
        )

        if not query or not text:
            return MatchResult(matched=False)

        threshold = (
            flt.threshold
            if flt.threshold is not None
            else config.DEFAULT_TOPIC_THRESHOLD
        )

        # Защищаем загрузку модели и кэши при параллельных вызовах.
        with self._lock:
            article_vector = self._article_embedding(article.id, text)
            topic_vector = self._topic_embedding(query)

            # Для нормализованных векторов это косинусное сходство.
            score = float(np.sum(article_vector * topic_vector))

        # Учитываем погрешность вычислений на границах диапазона.
        score = max(-1.0, min(1.0, score))
        matched = score >= threshold

        reasons = []
        if matched:
            reasons.append(
                f"тема «{query}»: сходство {score:.3f}, "
                f"порог {threshold:.3f}"
            )

        return MatchResult(
            matched=matched,
            score=score,
            reasons=reasons,
        )
