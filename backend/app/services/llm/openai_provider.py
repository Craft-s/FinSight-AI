from openai import OpenAI

from app.services.llm.base import (
    BaseProvider,
    model_record,
    normalize_error,
    provider_error,
    usage_counts,
)


class OpenAIProvider(BaseProvider):
    provider = "openai"
    base_url = "https://api.openai.com/v1"

    def _client(self, http_client, options=None):
        resolved = options or self.options
        return OpenAI(api_key=self.api_key, base_url=self.base_url, http_client=http_client,
                      max_retries=0, timeout=resolved["timeout_seconds"])

    def list_models(self) -> list[dict]:
        try:
            with self._http_client() as http_client, self._client(http_client) as client:
                result = []
                for model in client.models.list():
                    if len(result) >= 1000:
                        raise provider_error("INVALID_RESPONSE")
                    if not model.id:
                        raise provider_error("INVALID_RESPONSE")
                    result.append(model_record(self.provider, model.id))
                return result
        except Exception as exc:
            raise normalize_error(exc) from None

    def _generate(self, model_id, system, prompt, options, structured):
        # Responses is the text endpoint. Unknown IDs are tried without guessed capabilities.
        if structured:
            system += "\nReturn only a JSON object matching the requested schema."
        with self._http_client(options) as http_client, self._client(http_client, options) as client:
            advanced = {}
            if "reasoning_effort" in options:
                advanced["reasoning"] = {"effort": options["reasoning_effort"]}
            if "temperature" in options:
                advanced["temperature"] = options["temperature"]
            response = client.responses.create(
                model=model_id, instructions=system, input=prompt,
                max_output_tokens=options["max_output_tokens"], store=False, **advanced,
            )
            body = response.model_dump(mode="json")
            if body.get("status") != "completed" or body.get("error"):
                raise provider_error("INVALID_RESPONSE")
            content = []
            for item in body.get("output", []):
                if item.get("type") == "message":
                    if item.get("status") != "completed":
                        raise provider_error("INVALID_RESPONSE")
                    for part in item.get("content", []):
                        if part.get("type") == "refusal":
                            raise provider_error("INVALID_RESPONSE")
                        if part.get("type") == "output_text":
                            content.append(part["text"])
                elif item.get("type") != "reasoning":
                    raise provider_error("INVALID_RESPONSE")
            usage = body.get("usage") or {}
            return {"content": "".join(content), "usage": usage_counts(
                input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                total_tokens=usage.get("total_tokens"),
            )}
