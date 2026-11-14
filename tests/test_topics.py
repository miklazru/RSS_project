import numpy as np
import pytest

from app.matching.base import ArticleData, FilterData
from app.matching.semantic import TopicMatcher


class FakeModel:
    """Известные нормализованные векторы для проверки логики."""

    def __init__(self):
        self.calls = []

    def encode(self, text, **kwargs):
        self.calls.append(text)

        if text in {"Технологии", "Экономика"}:
            return np.array([1.0, 0.0])

        # Сходство с вектором темы равно 0.8.
        return np.array([0.8, 0.6])


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