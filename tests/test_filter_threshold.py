"""Изменение порога сходства у уже созданной темы: PATCH /api/users/{id}/filters/{filter_id}."""
import numpy as np
import pytest

from app import config, matching
from app.matching.semantic import TopicMatcher


class GradedModel:
    """Подмена модели: сходство с темой «космос» — 1.0, 0.6 или 0.0 в зависимости от текста."""

    @staticmethod
    def _vector(text):
        text = text.lower()
        if "космос" in text:
            return [1.0, 0.0]
        if "рынок" in text:
            return [0.6, 0.8]
        return [0.0, 1.0]

    def encode(self, texts, **kwargs):
        if isinstance(texts, str):
            return np.array(self._vector(texts), dtype=np.float32)
        return np.array([self._vector(t) for t in texts], dtype=np.float32)


@pytest.fixture
def graded_topics(monkeypatch, client):
    monkeypatch.setattr(TopicMatcher, "_load_model", lambda self: GradedModel())
    monkeypatch.setitem(matching._matchers, "topic", TopicMatcher())


@pytest.fixture
def space_user(graded_topics, make_user, add_filter, post):
    user = make_user("Аня")
    flt = add_filter(user["id"], "topic", "космос", threshold=0.9)
    post("Полёт в космос")      # сходство 1.0
    post("Рынок вырос")         # сходство 0.6
    post("Погода на выходные")  # сходство 0.0
    return user, flt


def patch(client, user_id, filter_id, threshold):
    return client.patch(f"/api/users/{user_id}/filters/{filter_id}", json={"threshold": threshold})


def titles(client, user_id):
    return {e["title"] for e in client.get(f"/api/users/{user_id}/feed").json()}


def test_filter_out_reports_threshold_and_count(client, space_user):
    user, flt = space_user
    assert flt["effective_threshold"] == 0.9
    [listed] = client.get(f"/api/users/{user['id']}/filters").json()
    assert listed["threshold"] == 0.9
    assert listed["effective_threshold"] == 0.9
    assert listed["matches_count"] == 1


def test_lowering_threshold_adds_articles(client, space_user):
    user, flt = space_user
    assert titles(client, user["id"]) == {"Полёт в космос"}

    response = patch(client, user["id"], flt["id"], 0.5)
    assert response.status_code == 200, response.text
    assert response.json()["threshold"] == 0.5
    assert response.json()["matches_count"] == 2
    assert titles(client, user["id"]) == {"Полёт в космос", "Рынок вырос"}

    # причина в ленте показывает новый порог
    entry = next(e for e in client.get(f"/api/users/{user['id']}/feed").json() if e["title"] == "Рынок вырос")
    assert "порог 0.500" in entry["matches"][0]["reasons"][0]


def test_raising_threshold_removes_articles(client, space_user):
    user, flt = space_user
    patch(client, user["id"], flt["id"], 0.0)
    assert len(titles(client, user["id"])) == 3

    response = patch(client, user["id"], flt["id"], 0.95)
    assert response.json()["matches_count"] == 1
    assert titles(client, user["id"]) == {"Полёт в космос"}


def test_null_resets_to_default_threshold(client, space_user, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_TOPIC_THRESHOLD", 0.5)
    user, flt = space_user
    response = patch(client, user["id"], flt["id"], None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["threshold"] is None
    assert body["effective_threshold"] == 0.5
    assert body["matches_count"] == 2


def test_new_articles_use_updated_threshold(client, space_user, post):
    user, flt = space_user
    patch(client, user["id"], flt["id"], 0.5)
    post("Рынок упал")
    assert "Рынок упал" in titles(client, user["id"])


def test_update_errors(client, space_user, make_user, add_filter):
    user, flt = space_user
    keyword = add_filter(user["id"], "keyword", "космос")
    other = make_user("Боря")

    assert patch(client, user["id"], keyword["id"], 0.5).status_code == 400
    assert patch(client, other["id"], flt["id"], 0.5).status_code == 404
    assert patch(client, user["id"], 99999, 0.5).status_code == 404
    assert patch(client, user["id"], flt["id"], 1.5).status_code == 422
    assert patch(client, user["id"], flt["id"], -0.1).status_code == 422
