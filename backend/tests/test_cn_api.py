"""TEST ONLY synthetic CN responses; all data lives in the disposable API fixture."""
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.db.cn_models import CnCompany, CnDataset, CnSnapshot, CnSyncJob
from app.db.session import session_factory
from app.services.cn import service
from app.services.cn.provider import Resource
from app.services.cn.service import ensure_cn_dataset, import_company_resources, import_directory
from tests.conftest import TEST_TOKEN

STAMP = datetime(2026, 1, 1, tzinfo=UTC)
SYMBOL = "600519.SH"


def resource(kind, rows, *, stamp=STAMP):
    body = {"success": True, "result": {"data": rows, "pages": 1, "count": len(rows)}}
    return Resource(kind=kind, url="https://datacenter-web.eastmoney.com/api/data/v1/get",
                    raw=json.dumps(body).encode(), fetched_at=stamp, metadata={"test_only": True})


def directory():
    return Resource(kind="directory", url="https://www.cninfo.com.cn/new/data/szse_stock.json",
                    raw=json.dumps({"stockList": [{"code": "600519", "zwjc": "TEST ONLY 合成公司",
                                                   "orgId": "gssh0600519", "category": "A股"}]}).encode(),
                    fetched_at=STAMP, metadata={"test_only": True})


def row(end="2025-12-31", **fields):
    return {"SECUCODE": SYMBOL, "SECURITY_CODE": "600519", "CURRENCY": "CNY",
            "ORG_TYPE": "通用", "REPORT_DATE": end, "NOTICE_DATE": "2026-01-01",
            "UPDATE_DATE": "2026-01-01", "REPORT_TYPE": "合并", "REPORT_TYPE_CODE": "001", **fields}


def financial_resources(amount=100):
    return [resource("indicators", [row()]), resource("income", [row(TOTAL_OPERATE_INCOME=amount,
                     OPERATE_INCOME=amount, OPERATE_COST=40, NETPROFIT=20, PARENT_NETPROFIT=18)]),
            resource("balance", [row(TOTAL_ASSETS=200, TOTAL_LIABILITIES=80, TOTAL_EQUITY=120)]),
            resource("cashflow", [row(NETCASH_OPERATE=25, END_CCE=50)])]


@pytest.fixture
def cn_seeded(api):
    with session_factory()() as session:
        ensure_cn_dataset(session)
        import_directory(session, [directory()])
        import_company_resources(session, session.get(CnCompany, SYMBOL), financial_resources())
        session.commit()
    return api


def test_market_namespace_search_and_source_binding(cn_seeded):
    response = cn_seeded.get("/api/v1/cn/companies/search", params={"q": "600519"})
    assert response.status_code == 200
    company = response.json()["items"][0]
    assert company["symbol"] == SYMBOL and company["market"] == "CN"
    assert company["currency"] == "CNY" and "cik" not in company
    assert company["source_kind"] == "synthetic_fixture"
    assert cn_seeded.get("/api/v1/companies/search").json()["items"] == []
    assert cn_seeded.get(f"/api/v1/companies/{SYMBOL}").status_code == 422
    assert cn_seeded.get("/api/v1/cn/companies/search", params={"q": "%' OR 1=1 --"}).json()["items"] == []
    assert cn_seeded.get("/api/v1/cn/companies/search", params={"exchange": "HK"}).status_code == 422


def test_currency_period_source_and_unsupported_history(cn_seeded):
    annual = cn_seeded.get(f"/api/v1/cn/companies/{SYMBOL}/metrics").json()
    assert annual["currency"] == "CNY" and annual["selection_policy"] == "latest_provider_snapshot"
    income = next(item for item in annual["items"] if item["metric_key"] == "cn_total_revenue")
    assert income["value"] == "100" and income["period_type"] == "annual"
    assert income["provenance"]["source_field"] == "TOTAL_OPERATE_INCOME"
    source = cn_seeded.get("/api/v1/cn/sources/" + income["provenance"]["source_snapshot_id"]).json()
    assert len(source["sha256"]) == 64 and "raw_json" not in source
    assert source["source_metadata"]["test_only"] is True
    for params in ({"frequency": "quarterly"}, {"selection_policy": "as_of", "as_of": "2025-12-31"},
                   {"accession": "test"}, {"years": 11}):
        assert cn_seeded.get(f"/api/v1/cn/companies/{SYMBOL}/metrics", params=params).status_code == 422
    cumulative = cn_seeded.get(f"/api/v1/cn/companies/{SYMBOL}/metrics?frequency=cumulative").json()
    assert cumulative["items"][0]["period_type"] == "cumulative"


def test_all_financial_pages_are_published_as_one_bundle(cn_seeded):
    with session_factory()() as session:
        company = session.get(CnCompany, SYMBOL)
        pages = financial_resources()
        older = resource("income", [row("2024-12-31", TOTAL_OPERATE_INCOME=80)],
                         stamp=datetime(2026, 1, 2, tzinfo=UTC))
        pages.append(older)
        import_company_resources(session, company, pages)
        session.commit()
        old_id = company.source_metadata["current_source_ids"]["income"][1]
    items = cn_seeded.get(f"/api/v1/cn/companies/{SYMBOL}/metrics").json()["items"]
    values = {item["period_end"]: item for item in items if item["metric_key"] == "cn_total_revenue"}
    assert values["2024-12-31"]["value"] == "80"
    assert values["2024-12-31"]["provenance"]["source_snapshot_id"] == old_id
    assert Decimal(values["2025-12-31"]["yoy"]) == Decimal("25")


def test_repeated_bytes_keep_source_versions_and_directory_refresh_keeps_financial_pointer(cn_seeded):
    with session_factory()() as session:
        company = session.get(CnCompany, SYMBOL)
        previous = dict(company.source_metadata["current_source_ids"])
        count = session.scalar(select(func.count()).select_from(CnSnapshot))
        import_company_resources(session, company, financial_resources())
        import_directory(session, [directory()])
        session.commit()
        assert session.scalar(select(func.count()).select_from(CnSnapshot)) == count
        assert company.source_metadata["current_source_ids"] == previous


def test_missing_page_manifest_is_not_mixed_with_latest_source(cn_seeded):
    with session_factory()() as session:
        company = session.get(CnCompany, SYMBOL)
        company.source_metadata = {"current_source_ids": {"income": ["missing-source"]}}
        session.commit()
    response = cn_seeded.get(f"/api/v1/cn/companies/{SYMBOL}/metrics")
    assert response.status_code == 503 and response.json()["error"]["code"] == "CN_SOURCE_MANIFEST_INVALID"


def test_incomplete_sync_rolls_back_its_new_versions(cn_seeded):
    with session_factory()() as session:
        company = session.get(CnCompany, SYMBOL)
        previous = dict(company.source_metadata)
        with pytest.raises(AppError):
            import_company_resources(session, company, [resource("income", [row(TOTAL_OPERATE_INCOME=999)])])
        session.rollback()
        assert session.get(CnCompany, SYMBOL).source_metadata == previous


def test_whole_empty_statement_never_replaces_valid_financial_bundle(cn_seeded):
    with session_factory()() as session:
        company = session.get(CnCompany, SYMBOL)
        previous = dict(company.source_metadata)
        pages = [item for item in financial_resources() if item.kind != "balance"] + [resource("balance", [])]
        with pytest.raises(AppError) as error:
            import_company_resources(session, company, pages)
        assert error.value.code == "CN_FINANCIAL_DATA_INCOMPLETE"
        session.rollback()
        assert session.get(CnCompany, SYMBOL).source_metadata == previous


def test_cn_mode_mismatch_does_not_change_sec_dataset(api):
    with session_factory()() as session:
        session.add(CnDataset(id="singleton", source_kind="cn_live"))
        session.commit()
    response = api.get("/api/v1/cn/status")
    assert response.status_code == 503 and response.json()["error"]["code"] == "CN_DATA_MODE_MISMATCH"
    assert api.get("/api/v1/companies/search").status_code == 200


@pytest.mark.parametrize("method,path", [("POST", "/api/v1/cn/directory/sync"),
    ("POST", f"/api/v1/cn/companies/{SYMBOL}/sync"), ("GET", "/api/v1/cn/sync/jobs/test")])
def test_cn_private_endpoints_require_authorization(api, method, path):
    assert api.request(method, path).status_code == 401
    assert api.request(method, path, headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_fixture_mode_never_fetches_network(api, monkeypatch):
    called = []
    monkeypatch.setattr(service.CnClient, "fetch_directory", lambda *_: called.append(True))
    response = api.post("/api/v1/cn/directory/sync", headers={"X-Admin-Token": TEST_TOKEN})
    assert response.status_code == 422 and called == []


def test_queue_failure_is_persistent_and_releases_active_key(api, monkeypatch):
    from app.api import cn as cn_api
    from app.core.config import get_settings
    # Live orchestration only; this isolated test performs no upstream request.
    monkeypatch.setattr(service, "mode", lambda: "cn_live")
    monkeypatch.setattr(cn_api, "mode", lambda: "cn_live")
    monkeypatch.setattr(get_settings(), "task_mode", "rq")
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("TEST ONLY unavailable Redis")
    monkeypatch.setattr(cn_api.Redis, "from_url", unavailable)
    response = api.post("/api/v1/cn/directory/sync", headers={"X-Admin-Token": TEST_TOKEN})
    assert response.status_code == 503 and response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    with session_factory()() as session:
        job = session.scalar(select(CnSyncJob))
        assert job.status == "failed" and job.active_key is None
        assert session.scalar(select(func.count()).select_from(CnSnapshot)) == 0


def test_online_fetch_failure_keeps_all_old_source_versions(cn_seeded, monkeypatch):
    monkeypatch.setattr(service, "mode", lambda: "cn_live")
    def unavailable(*_args, **_kwargs):
        raise AppError("CN_NETWORK_ERROR", "TEST ONLY upstream unavailable", 503)
    monkeypatch.setattr(service.CnClient, "fetch_company", unavailable)
    with session_factory()() as session:
        session.get(CnDataset, "singleton").source_kind = "cn_live"
        previous = dict(session.get(CnCompany, SYMBOL).source_metadata)
        count = session.scalar(select(func.count()).select_from(CnSnapshot))
        job = CnSyncJob(scope=SYMBOL, active_key=SYMBOL, owner_id="local-owner")
        session.add(job)
        session.commit()
        identifier = job.id
    service.run_cn_sync(identifier)
    with session_factory()() as session:
        job = session.get(CnSyncJob, identifier)
        assert job.status == "failed" and job.error_code == "CN_NETWORK_ERROR" and job.active_key is None
        assert session.get(CnCompany, SYMBOL).source_metadata == previous
        assert session.scalar(select(func.count()).select_from(CnSnapshot)) == count


@pytest.mark.parametrize("body", [[], {"source_kind": "cn_snapshot", "scope": "directory", "resources": [1]},
    {"source_kind": "cn_snapshot", "scope": "directory", "resources": []}])
def test_invalid_archive_shape_is_rejected_with_rollback(api, monkeypatch, tmp_path, body):
    from app.services.cn import archive
    monkeypatch.setattr(service, "mode", lambda: "cn_snapshot")
    monkeypatch.setattr(archive, "mode", lambda: "cn_snapshot")
    manifest = tmp_path / "TEST_ONLY_manifest.json"
    manifest.write_text(json.dumps(body), encoding="utf-8")
    with session_factory()() as session:
        with pytest.raises(AppError) as error:
            archive.import_manifest(session, manifest)
        assert error.value.code == "CN_MANIFEST_INVALID"
        assert session.scalar(select(func.count()).select_from(CnSnapshot)) == 0


def test_duplicate_delivery_and_failed_fetch_preserve_previous_data(cn_seeded, monkeypatch):
    # The explicitly isolated synthetic API database permits exercising live
    # orchestration with a network stub; production never receives this data.
    monkeypatch.setattr(service, "mode", lambda: "synthetic_fixture")
    with session_factory()() as session:
        job = CnSyncJob(scope=SYMBOL, active_key=SYMBOL, owner_id="local-owner", status="running")
        session.add(job)
        session.commit()
        identifier = job.id
    called = []
    monkeypatch.setattr(service.CnClient, "fetch_company", lambda *_a, **_k: called.append(True))
    service.run_cn_sync(identifier)
    assert called == []
    with session_factory()() as session:
        job = session.get(CnSyncJob, identifier)
        job.status = "queued"
        session.commit()
    service.run_cn_sync(identifier)
    result = cn_seeded.get("/api/v1/cn/sync/jobs/" + identifier, headers={"X-Admin-Token": TEST_TOKEN})
    assert result.json()["status"] == "failed" and result.json()["error_code"] == "CN_LIVE_DISABLED"
    assert cn_seeded.get(f"/api/v1/cn/companies/{SYMBOL}/metrics").json()["items"]
