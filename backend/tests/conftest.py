import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_engine, session_factory
from app.services.sync import import_company

TEST_TOKEN = "finsight-test-only-token-never-production"
FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


@pytest.fixture
def api(tmp_path, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    if not url.startswith("sqlite") and "_test" not in url.rsplit("/", 1)[-1]:
        raise RuntimeError("TEST_DATABASE_URL must use a dedicated database with '_test' in its name")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DEVELOPMENT", "true")
    monkeypatch.setenv("DATA_MODE", "synthetic_fixture")
    monkeypatch.setenv("TASK_MODE", "inline")
    monkeypatch.setenv("LOCAL_ADMIN_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("SNAPSHOT_DIRECTORY", "")
    get_settings.cache_clear()
    get_engine.cache_clear()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    command.downgrade(config, "base")
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def seeded(api):
    from datetime import UTC, datetime
    with session_factory()() as session:
        for ticker in ("aapl", "msft", "nvda"):
            import_company(
                session,
                (FIXTURES / f"{ticker}_submissions.json").read_bytes(),
                (FIXTURES / f"{ticker}_companyfacts.json").read_bytes(),
                datetime(2026, 1, 1, tzinfo=UTC),
            )
        session.commit()
    return api
