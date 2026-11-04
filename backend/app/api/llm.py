from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

from app.core.auth import require_owner
from app.db.session import get_session
from app.services import llm_configs as service

router = APIRouter(prefix="/api/v1/llm", tags=["Model configuration"])
Owner = Annotated[str, Depends(require_owner)]
Database = Annotated[Session, Depends(get_session)]


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_output_tokens: int = Field(default=2048, ge=16, le=8192)
    timeout_seconds: float = Field(default=30, ge=5, le=120)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None


class SaveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=36)
    version: int | None = Field(default=None, ge=1)
    provider: Literal["openai", "deepseek", "gemini", "ollama"]
    model_id: str = Field(default="", max_length=200)
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    options: Options = Field(default_factory=Options)

    @field_validator("model_id")
    @classmethod
    def model_id_has_no_controls(cls, value):
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Invalid model ID")
        return value.strip()


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class ModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: bool = False


class ConfigOut(BaseModel):
    id: str
    provider: str
    model_id: str
    version: int
    verified_version: int | None
    last_tested_at: datetime | None
    is_verified: bool
    is_active: bool
    has_api_key: bool
    options: dict
    created_at: datetime
    updated_at: datetime


class ConfigListOut(BaseModel):
    items: list[ConfigOut]


class TestOut(BaseModel):
    config: ConfigOut
    usage: dict
    duration_ms: int


class ProviderStatus(BaseModel):
    id: str
    mode: Literal["cloud", "local"]
    label: str
    configured: bool
    options: list[str]


class StatusOut(BaseModel):
    available: bool
    encryption_ready: bool
    ollama_configured: bool
    providers: list[ProviderStatus]


class ModelInfo(BaseModel):
    id: str
    display_name: str
    provider: str
    supports_text_generation: bool | None = None
    context_window: int | None = None


class ModelsOut(BaseModel):
    items: list[ModelInfo]
    cached: bool


class DeleteOut(BaseModel):
    deleted: Literal[True]


@router.get("/status", response_model=StatusOut)
def status(session: Database):
    return service.status(session)


@router.get("/configs", response_model=ConfigListOut)
def configurations(owner: Owner, session: Database):
    return {"items": service.list_configs(session, owner)}


@router.post("/configs", response_model=ConfigOut)
def save_config(body: SaveConfig, owner: Owner, session: Database):
    return service.save_config(session, owner, body)


@router.post("/configs/{config_id}/models", response_model=ModelsOut)
def models(config_id: str, body: ModelsRequest, owner: Owner, session: Database):
    return service.models_for_config(session, owner, config_id, body.refresh)


@router.post("/configs/{config_id}/test", response_model=TestOut)
def test(config_id: str, body: VersionRequest, owner: Owner, session: Database):
    return service.test_config(session, owner, config_id, body.version)


@router.post("/configs/{config_id}/activate", response_model=ConfigOut)
def activate(config_id: str, body: VersionRequest, owner: Owner, session: Database):
    return service.activate_config(session, owner, config_id, body.version)


@router.post("/configs/{config_id}/delete", response_model=DeleteOut)
def delete(config_id: str, body: VersionRequest, owner: Owner, session: Database):
    return service.delete_config(session, owner, config_id, body.version)
