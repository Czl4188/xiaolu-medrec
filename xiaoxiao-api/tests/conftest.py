import os
import pathlib
import sys
import uuid

# 必须在导入 app 之前设置：settings 是模块级单例
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = ROOT / "data" / "test.db"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["APP_ENV"] = "dev"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["LLM_PROVIDER"] = "none"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _phone() -> str:
    """每次注册用不同的手机号：手机号是唯一身份。"""
    n = uuid.uuid4().int % 10**8
    return "138" + f"{n:08d}"


def _auth(client, username, password="pass12345", phone=None):
    phone = phone or _phone()
    r = client.post(
        "/api/auth/register",
        json={"username": username, "phone": phone, "password": password},
    )
    if r.status_code == 409:
        r = client.post("/api/auth/login", json={"phone": phone, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def auth_headers(client):
    return _auth(client, "测试用户甲")


@pytest.fixture(scope="session")
def other_headers(client):
    return _auth(client, "测试用户乙")


@pytest.fixture
def fresh_headers(client):
    """每个用例一个干净账号，避免会话级夹具把上一条用例的记录带进来。"""
    return _auth(client, "复验" + uuid.uuid4().hex[:8])
