"""Isolated browser-test server using actual archived SEC data, never root .env.

SQLite is a temporary development-only default. CI may use a dedicated PostgreSQL
database whose name ends with _e2e_test; no database/schema is dropped by this tool.
No external model/SEC calls or production credentials are used.
"""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from sqlalchemy.engine import make_url

    with TemporaryDirectory(prefix="finsight-e2e-") as temporary:
        # BaseSettings normally reads .env in cwd. Use an empty directory and
        # override every credential-bearing/runtime setting before any imports.
        os.chdir(temporary)
        database = os.getenv("FINSIGHT_E2E_DATABASE_URL", f"sqlite:///{Path(temporary).as_posix()}/snapshot-e2e.db")
        url = make_url(database)
        if url.get_backend_name() != "sqlite" and (
            url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_e2e_test")
        ):
            raise RuntimeError("FINSIGHT_E2E_DATABASE_URL must be SQLite or a dedicated PostgreSQL database ending _e2e_test")
        # Do not accept an arbitrary existing SQLite file from the environment.
        if url.get_backend_name() == "sqlite" and "FINSIGHT_E2E_DATABASE_URL" in os.environ:
            raise RuntimeError("SQLite E2E always uses a new temporary database; unset FINSIGHT_E2E_DATABASE_URL")
        os.environ.update({
            "DATABASE_URL": database, "DATA_MODE": "sec_snapshot", "DEVELOPMENT": "true",
            "TASK_MODE": "inline", "REDIS_URL": os.getenv("FINSIGHT_E2E_REDIS_URL", "redis://127.0.0.1:1/14"),
            "LOCAL_ADMIN_TOKEN": "finsight-e2e-disposable-only-token", "MASTER_ENCRYPTION_KEY": "",
            "OLLAMA_BASE_URL": "", "SEC_USER_AGENT": "", "CORS_ORIGINS": "http://127.0.0.1:4173",
            "SNAPSHOT_DIRECTORY": str(ROOT / "data" / "snapshots"),
        })
        import uvicorn
        from alembic import command
        from alembic.config import Config
        from app.cli import import_document_manifest
        from app.db.session import ensure_dataset, get_engine, session_factory
        from app.services.sync import import_company, load_snapshot_directory

        migration = Config(str(ROOT / "backend" / "alembic.ini"))
        migration.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
        command.upgrade(migration, "head")
        with session_factory()() as session:
            ensure_dataset(session)
            for directory in sorted((ROOT / "data" / "snapshots").iterdir()):
                if (directory / "manifest.json").is_file():
                    import_company(session, *load_snapshot_directory(directory))
            session.commit()
            manifest = ROOT / "data" / "documents" / "0000320193" / "0000320193-24-000123" / "manifest.json"
            import_document_manifest(session, manifest)
        print("E2E API ready: isolated real SEC archive; no model configuration; development inline tasks.", flush=True)
        from app.main import app

        @app.get("/api/v1/_e2e/status")
        def e2e_status():
            return {"harness": "finsight-isolated-e2e", "database": url.get_backend_name(), "source_kind": "sec_snapshot"}

        host = os.getenv("FINSIGHT_E2E_API_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "0.0.0.0"}:
            raise RuntimeError("E2E API bind host must be 127.0.0.1 or 0.0.0.0")
        port = int(os.getenv("FINSIGHT_E2E_API_PORT", "18000"))
        if not 1024 <= port <= 65535 or port == 8000:
            raise RuntimeError("E2E API port must be unprivileged and must not use development port 8000")
        try:
            uvicorn.run(app, host=host, port=port, log_level="warning")
        finally:
            get_engine().dispose()


if __name__ == "__main__":
    main()
