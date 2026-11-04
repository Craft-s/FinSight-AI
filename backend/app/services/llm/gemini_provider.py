from google import genai
from google.genai import types

from app.services.llm.base import (
    BaseProvider,
    model_record,
    normalize_error,
    provider_error,
    usage_counts,
)


class GeminiProvider(BaseProvider):
    provider = "gemini"
    base_url = "https://generativelanguage.googleapis.com"

    def _client(self, http_client, options=None):
        resolved = options or self.options
        return genai.Client(api_key=self.api_key, vertexai=False, enterprise=False,
                            http_options=types.HttpOptions(
                                base_url=self.base_url, api_version="v1beta",
                                httpx_client=http_client,
                                timeout=int(resolved["timeout_seconds"] * 1000),
                                retry_options=types.HttpRetryOptions(attempts=1),
                            ))

    def list_models(self) -> list[dict]:
        try:
            with self._http_client() as http_client, self._client(http_client) as client:
                result = []
                # SDK's synchronous pager follows nextPageToken until exhausted.
                for index, model in enumerate(client.models.list(config={"page_size": 100})):
                    if index >= 1000:
                        raise provider_error("INVALID_RESPONSE")
                    actions = model.supported_actions
                    if actions is not None and "generateContent" not in actions:
                        continue
                    if not model.name:
                        raise provider_error("INVALID_RESPONSE")
                    identifier = model.name.removeprefix("models/")
                    result.append(model_record(self.provider, identifier, model.display_name,
                                               True if actions is not None else None,
                                               model.input_token_limit))
                return result
        except Exception as exc:
            raise normalize_error(exc) from None

    def _generate(self, model_id, system, prompt, options, structured):
        configuration = {"system_instruction": system, "max_output_tokens": options["max_output_tokens"]}
        if "temperature" in options:
            configuration["temperature"] = options["temperature"]
        if structured:
            configuration["response_mime_type"] = "application/json"
        with self._http_client(options) as http_client, self._client(http_client, options) as client:
            response = client.models.generate_content(
                model=model_id.removeprefix("models/"), contents=prompt,
                config=types.GenerateContentConfig(**configuration),
            )
            body = response.model_dump(mode="json")
            feedback = body.get("prompt_feedback") or {}
            if feedback.get("block_reason") not in (None, "BLOCKED_REASON_UNSPECIFIED"):
                raise provider_error("INVALID_RESPONSE")
            candidate = body["candidates"][0]
            if candidate.get("finish_reason") != "STOP":
                raise provider_error("INVALID_RESPONSE")
            content = []
            for part in candidate["content"]["parts"]:
                if not part.get("thought") and part.get("text"):
                    content.append(part["text"])
                if part.get("function_call") or part.get("executable_code"):
                    raise provider_error("INVALID_RESPONSE")
            usage = body.get("usage_metadata") or {}
            return {"content": "".join(content), "usage": usage_counts(
                input_tokens=usage.get("prompt_token_count"), output_tokens=usage.get("candidates_token_count"),
                reasoning_tokens=usage.get("thoughts_token_count"), total_tokens=usage.get("total_token_count"),
            )}
