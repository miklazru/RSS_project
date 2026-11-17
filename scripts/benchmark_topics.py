import json
from pathlib import Path
from time import perf_counter

from app.matching.base import ArticleData, FilterData
from app.matching.semantic import TopicMatcher


def main():
    root = Path(__file__).resolve().parents[1]
    data = json.loads(
        (root / "data" / "articles_export.json").read_text(
            encoding="utf-8"
        )
    )

    # Как при загрузке новых новостей: ID ещё не назначены.
    articles = [
        ArticleData(
            id=0,
            title=item["title"],
            summary=item.get("summary") or "",
        )
        for item in data
    ]

    matcher = TopicMatcher()

    started = perf_counter()
    matcher.preload()
    preload_seconds = perf_counter() - started

    # Считаем фактические вызовы encode у настоящей модели.
    model = matcher._model
    original_encode = model.encode
    calls = []

    def counted_encode(texts, **kwargs):
        calls.append(
            {
                "texts": 1 if isinstance(texts, str) else len(texts),
                "batch_size": kwargs.get("batch_size"),
            }
        )
        return original_encode(texts, **kwargs)

    model.encode = counted_encode

    try:
        started = perf_counter()
        matcher.warm_up(articles)
        warm_up_seconds = perf_counter() - started
        warm_up_calls = list(calls)
        calls.clear()

        flt = FilterData(
            id=1,
            kind="topic",
            query="Искусственный интеллект, нейросети и их применение",
            threshold=0.35,
        )

        started = perf_counter()
        results = [matcher.match(article, flt) for article in articles]
        match_seconds = perf_counter() - started
        match_calls = list(calls)
        calls.clear()

        started = perf_counter()
        matcher.warm_up(articles)
        repeated_results = [
            matcher.match(article, flt) for article in articles
        ]
        repeat_seconds = perf_counter() - started
        repeat_calls = list(calls)

        assert repeated_results == results
        assert len(warm_up_calls) == 1
        assert warm_up_calls[0]["batch_size"] == 64
        assert len(match_calls) == 1
        assert match_calls[0]["texts"] == 1
        assert repeat_calls == []

    finally:
        model.encode = original_encode

    print(f"Статей: {len(articles)}")
    print(f"Загрузка модели: {preload_seconds:.3f} с")
    print(f"Первый прогрев статей: {warm_up_seconds:.3f} с")
    print(f"Сопоставление с новой темой: {match_seconds:.3f} с")
    print(
        "Прогрев + сопоставление: "
        f"{warm_up_seconds + match_seconds:.3f} с"
    )
    print(f"Повторный проход из кэша: {repeat_seconds:.3f} с")
    print(f"Вызовы encode при прогреве: {warm_up_calls}")
    print(f"Вызовы encode при сопоставлении: {match_calls}")
    print(f"Вызовы encode при повторе: {repeat_calls}")
    print(f"Совпадений: {sum(result.matched for result in results)}")
    print("Проверка пакетного кодирования и кэша пройдена.")


if __name__ == "__main__":
    main()