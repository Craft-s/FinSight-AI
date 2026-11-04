from app.core.errors import AppError
from app.services.llm.deepseek_provider import DeepSeekProvider
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider

PROVIDERS = {"openai": OpenAIProvider, "deepseek": DeepSeekProvider,
             "gemini": GeminiProvider, "ollama": OllamaProvider}


def create_provider(provider: str, api_key: str | None = None, base_url: str | None = None,
                    options: dict | None = None, *, transport=None):
    if provider not in PROVIDERS:
        raise AppError("MODEL_NOT_SUPPORTED", "当前模型服务商不受支持。", 422)
    return PROVIDERS[provider](api_key, base_url, options, transport=transport)
