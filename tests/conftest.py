import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app


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
        response = client.post("/api/users", json={"name": name})
        assert response.status_code == 201, response.text
        return response.json()

    return _make_user


@pytest.fixture
def add_filter(client):
    def _add_filter(user_id, kind, query, threshold=None):
        payload = {"kind": kind, "query": query}
        if threshold is not None:
            payload["threshold"] = threshold
        response = client.post(f"/api/users/{user_id}/filters", json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    return _add_filter


@pytest.fixture
def post(client):
    def _post(title, body=""):
        response = client.post("/api/own-channel/posts", json={"title": title, "body": body})
        assert response.status_code == 201, response.text
        return response.json()

    return _post


@pytest.fixture
def feed(client):
    def _feed(user_id):
        response = client.get(f"/api/users/{user_id}/feed")
        assert response.status_code == 200, response.text
        return response.json()

    return _feed
