import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://resume:resume@localhost:5434/resume")


@pytest.fixture(scope="session")
def dsn() -> str:
    return os.environ["DATABASE_URL"]


def sign_in(client, credentials):
    """Use an explicit takeover if a previous test left this demo account signed in."""
    response = client.post("/api/v1/auth/login", json=credentials)
    if response.status_code == 409 and response.json()["detail"]["code"] == "login_conflict":
        challenge = response.json()["detail"]["replace_login"]
        response = client.post("/api/v1/auth/login", json={**credentials,"replace_login":challenge})
    return response
