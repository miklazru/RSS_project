"""Интерфейс (роль 5): страница и статика раздаются самим FastAPI, без внешних ресурсов."""
import re

from app.main import STATIC_DIR


def test_index_serves_interface(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/static/app.js" in response.text
    assert "/static/style.css" in response.text


def test_static_files_served(client):
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]

    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")


def test_interface_works_offline():
    """На демо-ноутбуке может не быть интернета: никаких CDN, шрифтов и прочих внешних ссылок."""
    external = re.compile(r"""(?:src|href)\s*=\s*["']https?://|@import\s+url\(\s*["']?https?://|fetch\(\s*["']https?://""")
    for path in STATIC_DIR.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not external.search(text), f"внешний ресурс в {path.name}"


def test_interface_has_no_browser_dialogs():
    """prompt/confirm/alert молча блокируются во встроенных браузерах — кнопки «не работают»."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", js)
    assert not re.search(r"\b(prompt|confirm|alert)\s*\(", code)


def test_interface_uses_existing_api_routes(client):
    """Все пути API, к которым обращается интерфейс, есть в приложении."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r"""["'`](/api/[^"'`?]*)""", js))
    assert used, "интерфейс не обращается к API"

    routes = [route.path for route in client.app.routes if route.path.startswith("/api/")]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", path) + "$") for path in routes]
    for path in used:
        # ${state.userId} и подобное в шаблонной строке -> один сегмент пути
        concrete = re.sub(r"\$\{[^}]+\}", "1", path)
        assert any(p.match(concrete) for p in patterns), f"нет такого API: {path}"
