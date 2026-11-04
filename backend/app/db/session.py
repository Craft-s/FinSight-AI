import os
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Dataset


@lru_cache
def get_engine():
    url = get_settings().database_url
    engine = create_engine(
        url, pool_pre_ping=True,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    engine._finsight_pid = os.getpid()
    return engine


def session_factory():
    engine = get_engine()
    if engine._finsight_pid != os.getpid():
        # RQ's forked child must not share the parent's database connection pool.
        engine.dispose(close=False)
        engine._finsight_pid = os.getpid()
    return sessionmaker(engine, expire_on_commit=False)


def ensure_dataset(session):
    mode = get_settings().data_mode
    record = session.get(Dataset, "singleton")
    if record and record.source_kind != mode:
        raise AppError("DATA_MODE_MISMATCH", "数据库数据模式与配置不一致，请使用独立数据库。", 503)
    if not record:
        session.add(Dataset(id="singleton", source_kind=mode))
        session.commit()


def get_session():
    with session_factory()() as session:
        ensure_dataset(session)
        yield session
