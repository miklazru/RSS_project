import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter


def main():
    root = Path(__file__).resolve().parents[1]
    articles = json.loads(
        (root / "data" / "articles_export.json").read_text(
            encoding="utf-8"
        )
    )

    # Переменные задаются до импорта приложения.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["SCHEDULER_ENABLED"] = "0"

    with TemporaryDirectory(prefix="rss-topics-api-") as temp_dir:
        os.environ["DB_PATH"] = str(Path(temp_dir) / "benchmark.db")

        from fastapi.testclient import TestClient

        from app import db, matching
        from app.main import create_app
        from app.models import Article, Channel, Subscription

        db.configure(Path(os.environ["DB_PATH"]))

        started = perf_counter()

        try:
            with TestClient(
                create_app(start_scheduler=False)
            ) as client:
                startup_seconds = perf_counter() - started

                response = client.post(
                    "/api/users",
                    json={"name": "Проверка скорости тем"},
                )
                assert response.status_code == 201, response.text
                user_id = response.json()["id"]

                # Добавляем выгрузку напрямую в временную БД.
                # Фильтров ещё нет, эмбеддинги заранее не вычисляются.
                with db.session_scope() as session:
                    channel = Channel(
                        url="https://example.invalid/benchmark.xml",
                        title="Выгрузка для замера",
                        is_test=False,
                    )
                    session.add(channel)
                    session.flush()

                    session.add(
                        Subscription(
                            user_id=user_id,
                            channel_id=channel.id,
                        )
                    )

                    for number, item in enumerate(articles):
                        session.add(
                            Article(
                                channel_id=channel.id,
                                guid=f"benchmark-{number}",
                                title=item["title"],
                                summary=item.get("summary") or "",
                                link=item.get("link"),
                            )
                        )

                matcher = matching.get_matcher("topic")

                # Не допускаем тихого продолжения без модели,
                # если preload() при старте завершился ошибкой.
                assert matcher._model is not None, (
                    "Модель не загружена при старте приложения"
                )
                assert not matcher._article_cache, (
                    "Перед замером кэш статей должен быть пустым"
                )

                model = matcher._model
                original_encode = model.encode
                calls = []

                def counted_encode(texts, **kwargs):
                    calls.append(
                        {
                            "texts": (
                                1 if isinstance(texts, str)
                                else len(texts)
                            ),
                            "batch_size": kwargs.get("batch_size"),
                        }
                    )
                    return original_encode(texts, **kwargs)

                payload = {
                    "kind": "topic",
                    "query": (
                        "Искусственный интеллект, "
                        "нейросети и их применение"
                    ),
                    "threshold": 0.35,
                }

                model.encode = counted_encode

                try:
                    started = perf_counter()
                    response = client.post(
                        f"/api/users/{user_id}/filters",
                        json=payload,
                    )
                    first_seconds = perf_counter() - started

                    assert response.status_code == 201, response.text
                    filter_id = response.json()["id"]
                    first_calls = list(calls)
                    calls.clear()

                    # Другой фильтр с той же темой использует кэш,
                    # но API снова проходит статьи и сохраняет совпадения.
                    started = perf_counter()
                    response = client.post(
                        f"/api/users/{user_id}/filters",
                        json=payload,
                    )
                    repeat_seconds = perf_counter() - started

                    assert response.status_code == 201, response.text
                    repeat_calls = list(calls)

                finally:
                    model.encode = original_encode

                response = client.get(
                    f"/api/users/{user_id}/feed",
                    params={"limit": 500},
                )
                assert response.status_code == 200, response.text

                entries = response.json()
                assert entries, "Лента пуста: проверь логи движка"

                for entry in entries:
                    matches = [
                        match
                        for match in entry["matches"]
                        if match["filter_id"] == filter_id
                    ]
                    assert matches, "Нет совпадения первого фильтра"
                    assert all(
                        match["kind"] == "topic"
                        and match["reasons"]
                        and match["score"] >= payload["threshold"]
                        for match in matches
                    )

                unique_texts = {
                    "\n".join(
                        part.strip()
                        for part in (
                            item["title"],
                            item.get("summary") or "",
                        )
                        if part and part.strip()
                    )
                    for item in articles
                }
                unique_texts.discard("")

                assert first_calls == [
                    {"texts": len(unique_texts), "batch_size": 64},
                    {"texts": 1, "batch_size": 64},
                ], first_calls
                assert repeat_calls == [], repeat_calls

                print(f"\nСтатей в базе: {len(articles)}")
                print(f"Запуск приложения: {startup_seconds:.3f} с")
                print(
                    "Первое создание фильтра через API: "
                    f"{first_seconds:.3f} с"
                )
                print(
                    "Создание второго фильтра с той же темой: "
                    f"{repeat_seconds:.3f} с"
                )
                print(f"Вызовы encode при первом запросе: {first_calls}")
                print(f"Вызовы encode при втором запросе: {repeat_calls}")
                print(f"Статей в ленте: {len(entries)}")
                print("Проверка API пройдена.")

        finally:
            if db._engine is not None:
                db._engine.dispose()

    print("Временная база удалена. Рабочая база не изменялась.")


if __name__ == "__main__":
    main()