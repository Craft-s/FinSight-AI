from logging.config import fileConfig

from alembic import context

from app.core.config import get_settings
from app.db import (  # noqa: F401 - register all migration metadata
    cn_document_models,
    cn_models,
    cn_research_models,
    document_models,
    llm_models,
    overview_models,
    research_models,
)
from app.db.models import Base
from app.db.session import get_engine

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
