"""Проверки API: создание, валидация, ошибки."""


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_new_user_is_subscribed_to_own_channel(client, make_user):
    user = make_user("Аня")
    channels = client.get(f"/api/users/{user['id']}/channels").json()
    assert len(channels) == 1
    assert channels[0]["is_test"] is True


def test_duplicate_user_name_is_rejected(client, make_user):
    make_user("Аня")
    response = client.post("/api/users", json={"name": "Аня"})
    assert response.status_code == 409


def test_empty_user_name_is_rejected(client):
    assert client.post("/api/users", json={"name": "   "}).status_code == 422


def test_filter_validation(client, make_user):
    user = make_user()
    url = f"/api/users/{user['id']}/filters"
    assert client.post(url, json={"kind": "regex", "query": "x"}).status_code == 422
    assert client.post(url, json={"kind": "keyword", "query": "  "}).status_code == 422
    assert client.post(url, json={"kind": "topic", "query": "x", "threshold": 1.5}).status_code == 422


def test_keyword_filter_ignores_threshold(client, make_user, add_filter):
    user = make_user()
    flt = add_filter(user["id"], "keyword", "санкции", threshold=0.9)
    assert flt["threshold"] is None


def test_filter_of_other_user_cannot_be_deleted(client, make_user, add_filter):
    anya = make_user("Аня")
    borya = make_user("Боря")
    flt = add_filter(anya["id"], "keyword", "санкции")
    response = client.delete(f"/api/users/{borya['id']}/filters/{flt['id']}")
    assert response.status_code == 404


def test_unknown_user_returns_404(client):
    assert client.get("/api/users/999/feed").status_code == 404
    assert client.get("/api/users/999/filters").status_code == 404


def test_channel_url_must_be_http(client):
    response = client.post("/api/channels", json={"url": "ftp://example.com/feed"})
    assert response.status_code == 422


def test_add_channel_twice_returns_same_channel(client, monkeypatch):
    # чтобы тест не ходил в интернет, отключаем фоновое скачивание
    monkeypatch.setattr("app.api.fetch_channel_by_id", lambda channel_id: 0)
    first = client.post("/api/channels", json={"url": "https://example.com/rss"})
    second = client.post("/api/channels", json={"url": "https://example.com/rss"})
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_add_and_subscribe_in_one_step(client, make_user, monkeypatch):
    monkeypatch.setattr("app.api.fetch_channel_by_id", lambda channel_id: 0)
    user = make_user()
    response = client.post(
        f"/api/users/{user['id']}/channels", json={"url": "https://example.com/rss"}
    )
    assert response.status_code == 201
    urls = [c["url"] for c in client.get(f"/api/users/{user['id']}/channels").json()]
    assert "https://example.com/rss" in urls


def test_own_channel_cannot_be_deleted(client):
    own = [c for c in client.get("/api/channels").json() if c["is_test"]][0]
    assert client.delete(f"/api/channels/{own['id']}").status_code == 400


def test_deleting_user_removes_their_filters(client, make_user, add_filter):
    user = make_user()
    add_filter(user["id"], "keyword", "санкции")
    assert client.delete(f"/api/users/{user['id']}").status_code == 204
    assert client.get(f"/api/users/{user['id']}/filters").status_code == 404


def test_dates_are_returned_in_utc(client, make_user):
    user = make_user()
    created_at = client.get("/api/users").json()[0]["created_at"]
    # явный часовой пояс, чтобы фронтенд не сдвигал время
    assert created_at.endswith("Z") or created_at.endswith("+00:00")
