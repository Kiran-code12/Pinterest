import re
import warnings

import pytest

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import build_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.pinterest.mock import MockPinterestProvider  # noqa: E402

PASSWORD = "correct-horse-battery-1"


def make_settings(tmp_path, **extra):
    env = {"DATA_DIR": str(tmp_path), "ADMIN_PASSWORD": PASSWORD, "SECRET_KEY": "k" * 48,
           "PINTEREST_PROVIDER": "mock", "DIRECT_PUBLISHING_ENABLED": "true", "ACTION_RATE_LIMIT": "1000", "LOGIN_RATE_LIMIT": "50"}
    env.update(extra)
    return build_settings(env)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def mock_pin():
    return MockPinterestProvider()


@pytest.fixture
def app(settings, mock_pin):
    return create_app(settings, pinterest_provider=mock_pin)


@pytest.fixture
def db(app):
    with app.state.db.session() as s:
        yield s


class Web:
    """Tiny helper around TestClient that logs in and sends CSRF tokens automatically."""

    def __init__(self, app):
        self.c = TestClient(app, follow_redirects=False)
        self.app = app

    def token(self) -> str:
        html = self.c.get("/login").text
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def login(self, password=PASSWORD, username="admin"):
        return self.c.post("/login", data={"username": username, "password": password, "csrf_token": self.token()})

    def post(self, path, data=None, **kw):
        return self.c.post(path, data={**(data or {}), "csrf_token": self.token()}, **kw)

    def get(self, path, **kw):
        return self.c.get(path, **kw)

    def follow(self, resp):
        assert resp.status_code in (302, 303), resp.status_code
        return self.c.get(resp.headers["location"])

    def api_post(self, path, json=None):
        tok = self.c.get("/api/csrf").json()["csrf_token"]
        return self.c.post(path, json=json, headers={"X-CSRF-Token": tok})


@pytest.fixture
def web(app):
    w = Web(app)
    assert w.login().status_code == 303
    return w
