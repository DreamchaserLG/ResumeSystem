import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://resume:resume@localhost:5434/resume")


@pytest.fixture(scope="session")
def dsn() -> str:
    return os.environ["DATABASE_URL"]
