from app.services.llm.base import (
    BaseProvider,
    model_record,
    normalize_error,
    provider_error,
    usage_counts,
)


class DeepSeekProvider(BaseProvider):
    provider = "deepseek"
    base_url = "https://api.deepseek.com"

    def _request(self, method, path, *, options=None, body=None):
        with self._http_client(options) as client:
            response = client.request(method, self.base_url + path,
                                      headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise provider_error("INVALID_RESPONSE")
            return response.json()

    def list_models(self) -> list[dict]:
        try:
            body = self._request("GET", "/models")
            rows = body["data"]
            if not isinstance(rows, list) or len(rows) > 1000:
                raise provider_error("INVALID_RESPONSE")
            result = []
            for row in rows:
                identifier = row["id"]
                if not isinstance(identifier, str) or not identifier:
                    raise provider_error("INVALID_RESPONSE")
                # New optional fields are consumed only when explicitly provided.
                modalities = row.get("output_modalities")
                supports_text = "text" in modalities if isinstance(modalities, list) else None
                result.append(model_record(self.provider, identifier, row.get("name"),
                                           supports_text, row.get("context_window")))
            return result
        except Exception as exc:
            raise normalize_error(exc) from None

    def _generate(self, model_id, system, prompt, options, structured):
        body = {"model": model_id, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False, "max_tokens": options["max_output_tokens"]}
        if "reasoning_effort" in options:
            body["reasoning_effort"] = options["reasoning_effort"]
        if "temperature" in options:
            body["temperature"] = options["temperature"]
        if structured:
            body["messages"][0]["content"] += "\nReturn only a valid JSON object."
            body["response_format"] = {"type": "json_object"}
        response = self._request("POST", "/chat/completions", options=options, body=body)
        choice = response["choices"][0]
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
            raise provider_error("INVALID_RESPONSE")
        usage = response.get("usage") or {}
        return {"content": choice["message"]["content"], "usage": usage_counts(
            input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )}
