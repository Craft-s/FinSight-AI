from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, now, uid


class LLMConfig(Base):
    __tablename__ = "llm_configs"
    __table_args__ = (
        UniqueConstraint("owner_id", "provider", name="uq_llm_config_owner_provider"),
        Index("uq_llm_config_active_owner", "owner_id", unique=True,
              postgresql_where=text("is_active = true"), sqlite_where=text("is_active = 1")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(50), default="local-owner", index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str] = mapped_column(String(200), default="")
    encrypted_key: Mapped[str | None] = mapped_column(Text)
    options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1)
    verified_version: Mapped[int | None]
    verified_binding: Mapped[str | None] = mapped_column(String(64))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
