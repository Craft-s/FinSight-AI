from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://finsight@localhost:5432/finsight"
    redis_url: str = "redis://localhost:6379/0"
    data_mode: Literal["sec_live", "sec_snapshot", "synthetic_fixture"] = "sec_live"
    task_mode: Literal["rq", "inline"] = "rq"
    development: bool = False
    local_admin_token: SecretStr = SecretStr("")
    sec_user_agent: str = ""
    sec_timeout_seconds: float = 30
    sec_max_bytes: int = 30_000_000
    cache_ttl_seconds: int = 3600
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    snapshot_directory: str = ""
    # Invalid/missing AI-only settings must not disable financial data browsing.
    master_encryption_key: SecretStr = SecretStr("")
    ollama_base_url: str = ""
    llm_model_cache_seconds: int = Field(default=120, ge=0, le=600)
    document_max_bytes: int = Field(default=15_000_000, ge=100_000, le=30_000_000)
    document_max_chunks: int = Field(default=4000, ge=10, le=10_000)
    # Independent market binding; never changes or reuses SEC company identity.
    cn_data_mode: Literal["cn_live", "cn_snapshot", "synthetic_fixture"] = "cn_live"
    cn_timeout_seconds: float = Field(default=20, ge=5, le=60)
    cn_max_bytes: int = Field(default=10_000_000, ge=1_000_000, le=30_000_000)
    cn_pdf_max_bytes: int = Field(default=20_000_000, ge=100_000, le=30_000_000)
    cn_pdf_max_pages: int = Field(default=600, ge=1, le=1000)
    cn_pdf_timeout_seconds: float = Field(default=30, ge=5, le=120)
    cn_pdf_parse_timeout_seconds: float = Field(default=90, ge=10, le=180)
    cn_pdf_max_chars: int = Field(default=2_000_000, ge=10_000, le=5_000_000)
    cn_pdf_max_chunks: int = Field(default=4000, ge=10, le=10_000)

    @model_validator(mode="after")
    def development_only_fallbacks(self):
        if not self.development and (
            self.database_url.startswith("sqlite") or self.task_mode == "inline"
        ):
            raise ValueError("SQLite and inline tasks require DEVELOPMENT=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
