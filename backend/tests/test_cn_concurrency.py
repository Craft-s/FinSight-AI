"""TEST ONLY financial fixtures in a disposable PostgreSQL database.

SQLite cannot verify row locking. These cases deliberately keep stale ORM
objects alive while another transaction commits, rather than testing a single
session's already-current JSON. They never contact a financial data provider.
"""

import json
import os

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.db.cn_models import CnCompany
from app.db.session import session_factory
from app.services.cn.provider import Resource
from app.services.cn.service import (
    ensure_cn_dataset,
    import_company_resources,
    import_directory,
    metrics_for,
)
from tests.test_cn_api import SYMBOL, directory, financial_resources

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="Requires a dedicated PostgreSQL _test database; SQLite cannot verify transaction row locks",
)


@pytest.fixture
def cn_company(api):
    with session_factory()() as session:
        ensure_cn_dataset(session)
        import_directory(session, [directory()])
        import_company_resources(session, session.get(CnCompany, SYMBOL), financial_resources())
        session.commit()
    return SYMBOL


def changed_directory():
    original = directory()
    body = json.loads(original.raw)
    body["stockList"][0]["zwjc"] = "TEST ONLY revised directory name"
    return Resource(kind=original.kind, url=original.url, raw=json.dumps(body).encode(),
                    fetched_at=original.fetched_at, metadata=original.metadata)


def test_directory_refresh_reloads_stale_orm_metadata_before_preserving_financial_manifest(cn_company):
    sessions = session_factory()
    with sessions() as directory_session:
        stale = directory_session.get(CnCompany, cn_company)
        old_income = list(stale.source_metadata["current_source_ids"]["income"])
        with sessions() as financial_session:
            current = financial_session.get(CnCompany, cn_company)
            import_company_resources(financial_session, current, financial_resources(amount=200))
            financial_session.commit()
            published_income = list(current.source_metadata["current_source_ids"]["income"])
        assert old_income != published_income
        # This proves that the first session really has an obsolete ORM value.
        assert stale.source_metadata["current_source_ids"]["income"] == old_income
        import_directory(directory_session, [changed_directory()])
        directory_session.commit()
    with sessions() as verification:
        company = verification.get(CnCompany, cn_company)
        assert company.source_metadata["current_source_ids"]["income"] == published_income
        assert company.name == "TEST ONLY revised directory name"
        revenue = next(row for row in metrics_for(verification, cn_company, "annual", 5)
                       if row["metric_key"] == "cn_total_revenue")
        assert revenue["value"] == "200"


def test_financial_publish_reloads_stale_orm_metadata_before_preserving_directory_manifest(cn_company):
    sessions = session_factory()
    with sessions() as financial_session:
        stale = financial_session.get(CnCompany, cn_company)
        old_directory = list(stale.source_metadata["directory_source_ids"])
        with sessions() as directory_session:
            import_directory(directory_session, [changed_directory()])
            directory_session.commit()
            refreshed_directory = list(directory_session.get(CnCompany, cn_company).source_metadata["directory_source_ids"])
        assert old_directory != refreshed_directory
        assert stale.source_metadata["directory_source_ids"] == old_directory
        import_company_resources(financial_session, stale, financial_resources(amount=200))
        financial_session.commit()
    with sessions() as verification:
        current = verification.get(CnCompany, cn_company)
        assert current.source_metadata["directory_source_ids"] == refreshed_directory
        assert current.name == "TEST ONLY revised directory name"


@pytest.mark.parametrize("operation", ["directory", "financial"])
def test_both_publish_paths_hold_company_row_lock_until_transaction_finishes(cn_company, operation):
    sessions = session_factory()
    with sessions() as writer:
        if operation == "directory":
            import_directory(writer, [changed_directory()])
        else:
            import_company_resources(writer, writer.get(CnCompany, cn_company), financial_resources(amount=200))
        # A NOWAIT contender provides a deterministic PostgreSQL lock check:
        # no threads, sleeps, timing assumptions or long blocking test calls.
        with sessions() as contender, pytest.raises(OperationalError) as error:
            contender.scalar(select(CnCompany).where(CnCompany.symbol == cn_company)
                             .with_for_update(nowait=True))
        assert getattr(error.value.orig, "sqlstate", None) == "55P03"
        writer.commit()
    # Successful commit releases the lock and preserves normal read access.
    with sessions() as contender:
        assert contender.scalar(select(CnCompany).where(CnCompany.symbol == cn_company)
                                .with_for_update(nowait=True)).symbol == cn_company
