"""Семантическая фильтрация через локальную мультиязычную MiniLM."""

import os
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import List

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

    def __init__(self, model=None, cache_size=2000, batch_size=64):
        if cache_size < 1:
            raise ValueError("Размер кэша должен быть положительным")
        if batch_size < 1:
            raise ValueError("Размер пачки должен быть положительным")

        self._model = model
        self._batch_size = batch_size

        self._article_capacity = cache_size
        self._topic_capacity = cache_size

        self._article_cache = OrderedDict()
        self._topic_cache = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def _article_text(article: ArticleData) -> str:
        return "\n".join(
            part.strip()
            for part in (article.title, article.summary)
            if part and part.strip()
        )

    def _load_model(self):
        """Отдельный метод позволяет подставить модель в тестах."""
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

        return SentenceTransformer(
            str(model_path),
            device="cpu",
            local_files_only=True,
        )

    def preload(self) -> None:
        """Загрузить модель заранее. Повторный вызов безопасен."""
        with self._lock:
            if self._model is None:
                self._model = self._load_model()

    def _encode_many(self, texts: List[str]) -> np.ndarray:
        """Вычислить нормализованные эмбеддинги списка текстов."""
        self.preload()

        vectors = self._model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors, dtype=np.float32)

        if (
            vectors.ndim != 2
            or vectors.shape[0] != len(texts)
            or vectors.shape[1] == 0
        ):
            raise ValueError("Модель вернула неверную форму эмбеддингов")

        if not np.isfinite(vectors).all():
            raise ValueError("Модель вернула NaN или бесконечность")

        return vectors

    @staticmethod
    def _remember(cache, key, vector, capacity):
        cache[key] = vector
        cache.move_to_end(key)

        while len(cache) > capacity:
            cache.popitem(last=False)

    def warm_up(self, articles: List[ArticleData]) -> None:
        """Пакетно прогреть статьи до открытия транзакции БД.

        ID не используется: до записи в БД у разных статей id == 0.
        Повторяющиеся тексты кодируются один раз.

        Ёмкость кэша увеличивается до размера крупнейшего набора
        прогрева. Это позволяет сохранить всю пачку, даже если
        она больше начального cache_size.
        """
        texts = list(
            dict.fromkeys(
                text
                for article in articles
                if (text := self._article_text(article))
            )
        )

        if not texts:
            return

        with self._lock:
            self._article_capacity = max(
                self._article_capacity,
                len(texts),
            )

            missing = []

            for text in texts:
                if text in self._article_cache:
                    # Сначала защищаем уже прогретые тексты этой пачки
                    # от вытеснения при добавлении недостающих.
                    self._article_cache.move_to_end(text)
                else:
                    missing.append(text)

            if not missing:
                return

            vectors = self._encode_many(missing)

            for text, vector in zip(missing, vectors):
                self._remember(
                    self._article_cache,
                    text,
                    vector.copy(),
                    self._article_capacity,
                )

    def _article_embedding(self, text):
        if text in self._article_cache:
            self._article_cache.move_to_end(text)
            return self._article_cache[text]

        # Резервный путь: match() работает и без вызова warm_up().
        vector = self._encode_many([text])[0].copy()

        self._remember(
            self._article_cache,
            text,
            vector,
            self._article_capacity,
        )
        return vector

    def _topic_embedding(self, query):
        if query in self._topic_cache:
            self._topic_cache.move_to_end(query)
            return self._topic_cache[query]

        vector = self._encode_many([query])[0].copy()

        self._remember(
            self._topic_cache,
            query,
            vector,
            self._topic_capacity,
        )
        return vector

    def match(
        self,
        article: ArticleData,
        flt: FilterData,
    ) -> MatchResult:
        query = flt.query.strip()
        text = self._article_text(article)

        if not query or not text:
            return MatchResult(matched=False)

        threshold = (
            flt.threshold
            if flt.threshold is not None
            else config.DEFAULT_TOPIC_THRESHOLD
        )

        with self._lock:
            article_vector = self._article_embedding(text)
            topic_vector = self._topic_embedding(query)

            if article_vector.shape != topic_vector.shape:
                raise ValueError("Размерности эмбеддингов не совпадают")

            # Сумма произведений без matmul.
            score = float(
                np.sum(
                    article_vector.astype(np.float64)
                    * topic_vector.astype(np.float64)
                )
            )

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
