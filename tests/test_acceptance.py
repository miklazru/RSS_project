"""Сценарий приёмки препода, шаг за шагом (на нашем тестовом канале, без интернета)."""
from fastapi.testclient import TestClient

from app import db
from app.main import create_app


def titles(feed_items):
    return [item["title"] for item in feed_items]


def test_post_matching_filter_appears_with_reasons(make_user, add_filter, post, feed):
    user = make_user("Аня")
    add_filter(user["id"], "keyword", "санкции")

    post("Новые санкции против банков", "Подробности в материале")

    items = feed(user["id"])
    assert titles(items) == ["Новые санкции против банков"]
    match = items[0]["matches"][0]
    assert match["kind"] == "keyword"
    assert match["query"] == "санкции"
    # у каждой новости видно, почему она в ленте
    assert match["reasons"]
    assert "санкции" in match["reasons"][0]


def test_post_not_matching_filter_is_absent(make_user, add_filter, post, feed):
    user = make_user("Аня")
    add_filter(user["id"], "keyword", "санкции")

    post("Сборная выиграла матч", "Счёт 2:0")

    assert feed(user["id"]) == []


def test_keyword_phrase_reason_reaches_feed(make_user, add_filter, post, feed):
    user = make_user()
    add_filter(user["id"], "keyword", "искусственый интеллект")

    post("Об искусственном интеллекте")
    post("Искусственный материал", "Интеллект")

    items = feed(user["id"])
    assert titles(items) == ["Об искусственном интеллекте"]
    match = items[0]["matches"][0]
    assert match["score"] == 1.0
    assert match["reasons"] == [
        "фраза «искусственый интеллект» в заголовке: «искусственном интеллекте» "
        "(совпадение с опечаткой)"
    ]


def test_new_filter_applies_to_existing_articles(make_user, add_filter, post, feed):
    user = make_user("Аня")
    post("Курс рубль укрепился")
    assert feed(user["id"]) == []

    add_filter(user["id"], "keyword", "рубль")

    assert titles(feed(user["id"])) == ["Курс рубль укрепился"]


def test_feed_is_or_of_filters_and_lists_every_matched_filter(make_user, add_filter, post, feed):
    user = make_user("Аня")
    add_filter(user["id"], "keyword", "санкции")
    add_filter(user["id"], "keyword", "банки")

    post("Санкции и банки", "")
    post("Только санкции", "")
    post("Про погоду", "")

    items = {item["title"]: item for item in feed(user["id"])}
    assert set(items) == {"Санкции и банки", "Только санкции"}
    assert len(items["Санкции и банки"]["matches"]) == 2
    assert len(items["Только санкции"]["matches"]) == 1


def test_topic_filter_reports_score_and_threshold(make_user, add_filter, post, feed):
    user = make_user("Аня")
    add_filter(user["id"], "topic", "экономика инфляция ставка", threshold=0.3)

    post("Инфляция замедлилась", "Ставка осталась прежней")

    items = feed(user["id"])
    assert len(items) == 1
    match = items[0]["matches"][0]
    assert match["kind"] == "topic"
    assert 0.3 <= match["score"] <= 1.0
    assert "порог" in match["reasons"][0]


def test_users_see_only_their_own_feeds(make_user, add_filter, post, feed):
    anya = make_user("Аня")
    borya = make_user("Боря")
    add_filter(anya["id"], "keyword", "санкции")
    add_filter(borya["id"], "keyword", "сборная")

    post("Новые санкции")
    post("Сборная победила")

    assert titles(feed(anya["id"])) == ["Новые санкции"]
    assert titles(feed(borya["id"])) == ["Сборная победила"]


def test_unsubscribe_hides_channel_and_resubscribe_restores(client, make_user, add_filter, post, feed):
    user = make_user("Аня")
    add_filter(user["id"], "keyword", "санкции")
    post("Новые санкции")
    own_channel_id = client.get(f"/api/users/{user['id']}/channels").json()[0]["id"]

    assert client.delete(f"/api/users/{user['id']}/channels/{own_channel_id}").status_code == 204
    assert feed(user["id"]) == []

    assert client.put(f"/api/users/{user['id']}/channels/{own_channel_id}").status_code == 204
    assert titles(feed(user["id"])) == ["Новые санкции"]


def test_deleting_filter_removes_its_matches(client, make_user, add_filter, post, feed):
    user = make_user("Аня")
    flt = add_filter(user["id"], "keyword", "санкции")
    post("Новые санкции")

    response = client.delete(f"/api/users/{user['id']}/filters/{flt['id']}")

    assert response.status_code == 204
    assert feed(user["id"]) == []


def test_everything_survives_restart(db_path, make_user, add_filter, post, feed):
    """Шаг приёмки: сервис перезапускают — пользователи, фильтры, каналы и лента на месте."""
    user = make_user("Аня")
    add_filter(user["id"], "keyword", "санкции")
    post("Новые санкции")

    # «перезапуск»: новое приложение поверх того же файла базы
    db.configure(db_path)
    with TestClient(create_app(start_scheduler=False)) as restarted:
        users = restarted.get("/api/users").json()
        assert [u["name"] for u in users] == ["Аня"]
        filters = restarted.get(f"/api/users/{user['id']}/filters").json()
        assert [f["query"] for f in filters] == ["санкции"]
        feed_after = restarted.get(f"/api/users/{user['id']}/feed").json()
        assert titles(feed_after) == ["Новые санкции"]
        channels = restarted.get("/api/channels").json()
        # тестовый канал не задублировался при повторном старте
        assert sum(1 for c in channels if c["is_test"]) == 1
