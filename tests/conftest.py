import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db, fetcher, matching
from app.main import create_app
from app.matching.semantic import TopicMatcher


class FakeEmbeddingModel:
    """Подмена модели для общих тестов без скачивания весов.

    Постоянные векторы проверяют интеграцию приложения.
    Качество семантического поиска проверяется отдельно.
    """

    def encode(self, texts, **kwargs):
        if isinstance(texts, str):
            return np.array([1.0, 0.0], dtype=np.float32)

        return np.tile(
            np.array([1.0, 0.0], dtype=np.float32),
            (len(texts), 1),
        )


@pytest.fixture(autouse=True)
def offline_topic_model(monkeypatch):
    """Каждый тест получает движок с подставной моделью и чистым кэшем."""
    monkeypatch.setattr(
        TopicMatcher,
        "_load_model",
        lambda self: FakeEmbeddingModel(),
    )

    monkeypatch.setitem(
        matching._matchers,
        "topic",
        TopicMatcher(),
    )


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Повторы скачивания в тестах не должны ждать по-настоящему."""
    monkeypatch.setattr(fetcher, "_sleep", lambda seconds: None)


@pytest.fixture
def db_path(tmp_path):
    """Отдельная база на каждый тест."""
    path = tmp_path / "app.db"
    db.configure(path)
    return path


@pytest.fixture
def client(db_path):
    app = create_app(start_scheduler=False)

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user(client):
    def _make_user(name="Аня"):
        response = client.post(
            "/api/users",
            json={"name": name},
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _make_user


@pytest.fixture
def add_filter(client):
    def _add_filter(user_id, kind, query, threshold=None):
        payload = {
            "kind": kind,
            "query": query,
        }

        if threshold is not None:
            payload["threshold"] = threshold

        response = client.post(
            f"/api/users/{user_id}/filters",
            json=payload,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _add_filter


@pytest.fixture
def post(client):
    def _post(title, body=""):
        response = client.post(
            "/api/own-channel/posts",
            json={
                "title": title,
                "body": body,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _post


@pytest.fixture
def feed(client):
    def _feed(user_id):
        response = client.get(
            f"/api/users/{user_id}/feed"
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _feed
