import numpy as np
import pytest

from app.matching.base import ArticleData, FilterData
from app.matching.semantic import TopicMatcher


class FakeModel:
    """Поддерживает пакетное кодирование и записывает вызовы."""

    def __init__(self):
        self.calls = []
        self.batches = []

    def encode(self, texts, **kwargs):
        if isinstance(texts, str):
            raise AssertionError("Ожидался список текстов")

        texts = list(texts)
        self.calls.extend(texts)
        self.batches.append(
            {
                "texts": texts,
                "batch_size": kwargs.get("batch_size"),
            }
        )

        vectors = []

        for text in texts:
            if text in {"Технологии", "Экономика"}:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.8, 0.6])

        return np.array(vectors, dtype=np.float32)

@pytest.fixture
def engine():
    model = FakeModel()
    matcher = TopicMatcher(model=model)
    return matcher, model


def make_article(article_id=1, title="Новость", summary="Описание"):
    return ArticleData(
        id=article_id,
        title=title,
        summary=summary,
    )


def make_filter(query="Технологии", threshold=0.75):
    return FilterData(
        id=1,
        kind="topic",
        query=query,
        threshold=threshold,
    )


@pytest.mark.parametrize(
    "threshold, expected",
    [
        (0.79, True),
        (0.80, True),
        (0.81, False),
    ],
)
def test_threshold(engine, threshold, expected):
    matcher, _ = engine

    result = matcher.match(
        make_article(),
        make_filter(threshold=threshold),
    )

    assert result.score == pytest.approx(0.8)
    assert result.matched is expected

    if expected:
        assert len(result.reasons) == 1
        assert "Технологии" in result.reasons[0]
        assert "0.800" in result.reasons[0]
        assert f"{threshold:.3f}" in result.reasons[0]
    else:
        assert result.reasons == []


@pytest.mark.parametrize(
    "title, summary, query",
    [
        ("Новость", "Описание", "   "),
        ("", "   ", "Технологии"),
    ],
)
def test_empty_input_does_not_call_model(
    engine,
    title,
    summary,
    query,
):
    matcher, model = engine

    result = matcher.match(
        make_article(title=title, summary=summary),
        make_filter(query=query),
    )

    assert result.matched is False
    assert result.score == 0.0
    assert result.reasons == []
    assert model.calls == []


def test_title_without_summary(engine):
    matcher, model = engine

    matcher.match(
        make_article(title="Новая нейросеть", summary=""),
        make_filter(),
    )

    assert "Новая нейросеть" in model.calls


def test_repeated_match_uses_cache(engine):
    matcher, model = engine
    article = make_article()
    flt = make_filter()

    first = matcher.match(article, flt)
    second = matcher.match(article, flt)

    assert first == second
    assert len(model.calls) == 2


def test_changed_article_is_reencoded(engine):
    matcher, model = engine

    matcher.match(make_article(), make_filter())
    matcher.match(
        make_article(summary="Обновлённое описание"),
        make_filter(),
    )

    assert len(model.calls) == 3
    assert model.calls[-1] == "Новость\nОбновлённое описание"


def test_changed_topic_reuses_article_embedding(engine):
    matcher, model = engine

    matcher.match(
        make_article(),
        make_filter(query="Технологии"),
    )
    matcher.match(
        make_article(),
        make_filter(query="Экономика"),
    )

    assert len(model.calls) == 3
    assert model.calls[-1] == "Экономика"


def test_threshold_change_does_not_reencode(engine):
    matcher, model = engine

    first = matcher.match(
        make_article(),
        make_filter(threshold=0.7),
    )
    second = matcher.match(
        make_article(),
        make_filter(threshold=0.9),
    )

    assert first.matched is True
    assert second.matched is False
    assert len(model.calls) == 2


def test_article_cache_evicts_least_recently_used():
    model = FakeModel()
    matcher = TopicMatcher(model=model, cache_size=2)
    flt = make_filter()

    for article_id in (1, 2, 1, 3, 2):
        matcher.match(
            make_article(
                article_id=article_id,
                title=f"Новость {article_id}",
            ),
            flt,
        )

    article_calls = [
        text
        for text in model.calls
        if text.startswith("Новость ")
    ]

    # Повторное обращение к статье 1 сохраняет её в кэше.
    # Добавление статьи 3 вытесняет статью 2.
    assert article_calls == [
        "Новость 1\nОписание",
        "Новость 2\nОписание",
        "Новость 3\nОписание",
        "Новость 2\nОписание",
    ]


def test_default_threshold(engine, monkeypatch):
    matcher, _ = engine

    monkeypatch.setattr(
        "app.matching.semantic.config.DEFAULT_TOPIC_THRESHOLD",
        0.9,
    )

    result = matcher.match(
        make_article(),
        make_filter(threshold=None),
    )

    assert result.matched is False
    assert result.score == pytest.approx(0.8)

def test_preload_loads_model_once(monkeypatch):
    model = FakeModel()
    loads = []

    def load_model(self):
        loads.append(True)
        return model

    monkeypatch.setattr(TopicMatcher, "_load_model", load_model)

    matcher = TopicMatcher()

    matcher.preload()
    matcher.preload()

    assert len(loads) == 1
    assert model.calls == []


def test_warm_up_batches_articles_with_zero_ids():
    model = FakeModel()
    matcher = TopicMatcher(model=model)

    articles = [
        make_article(article_id=0, title="Первая статья"),
        make_article(article_id=0, title="Вторая статья"),
    ]

    matcher.warm_up(articles)

    assert model.batches == [
        {
            "texts": [
                "Первая статья\nОписание",
                "Вторая статья\nОписание",
            ],
            "batch_size": 64,
        }
    ]

    # После записи в БД ID изменились, тексты остались прежними.
    for article_id, article in enumerate(articles, start=101):
        matcher.match(
            make_article(
                article_id=article_id,
                title=article.title,
                summary=article.summary,
            ),
            make_filter(),
        )

    # После прогрева кодируется только тема.
    assert model.calls == [
        "Первая статья\nОписание",
        "Вторая статья\nОписание",
        "Технологии",
    ]


def test_warm_up_deduplicates_and_encodes_only_missing():
    model = FakeModel()
    matcher = TopicMatcher(model=model)

    first = make_article(article_id=0, title="Первая")
    second = make_article(article_id=0, title="Вторая")

    matcher.warm_up([first, first, second])
    matcher.warm_up([first, second])

    assert len(model.batches) == 1
    assert model.calls == [
        "Первая\nОписание",
        "Вторая\nОписание",
    ]

    matcher.warm_up(
        [
            first,
            make_article(article_id=0, title="Третья"),
        ]
    )

    assert len(model.batches) == 2
    assert model.batches[-1]["texts"] == ["Третья\nОписание"]


def test_warm_up_keeps_batch_larger_than_initial_cache():
    model = FakeModel()
    matcher = TopicMatcher(model=model, cache_size=2)

    articles = [
        make_article(article_id=0, title=f"Статья {number}")
        for number in range(5)
    ]

    matcher.warm_up(articles)

    for article in articles:
        matcher.match(article, make_filter())

    # Все пять статей посчитаны пачкой, тема — отдельно один раз.
    assert len(model.batches) == 2
    assert len(model.batches[0]["texts"]) == 5
    assert model.batches[1]["texts"] == ["Технологии"]


def test_warm_up_preserves_cached_members_of_batch():
    model = FakeModel()
    matcher = TopicMatcher(model=model, cache_size=2)

    first = make_article(title="Первая")
    unrelated = make_article(title="Посторонняя")
    second = make_article(title="Вторая")

    matcher.warm_up([first, unrelated])
    matcher.warm_up([first, second])

    calls_before = len(model.calls)

    matcher.match(first, make_filter())
    matcher.match(second, make_filter())

    # Статьи не вытеснились при прогреве; вычисляется только тема.
    assert model.calls[calls_before:] == ["Технологии"]


def test_empty_warm_up_does_not_load_model(monkeypatch):
    def unexpected_load(self):
        raise AssertionError("Пустому прогреву модель не нужна")

    monkeypatch.setattr(TopicMatcher, "_load_model", unexpected_load)

    matcher = TopicMatcher()

    matcher.warm_up([])
    matcher.warm_up(
        [make_article(title="  ", summary="")]
    )


def test_same_text_with_different_ids_uses_one_embedding(engine):
    matcher, model = engine

    matcher.match(make_article(article_id=1), make_filter())
    matcher.match(make_article(article_id=999), make_filter())

    assert model.calls == [
        "Новость\nОписание",
        "Технологии",
    ]
