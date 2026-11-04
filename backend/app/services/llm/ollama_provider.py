from app.services.llm.base import (
    BaseProvider,
    local_base_url,
    model_record,
    normalize_error,
    provider_error,
    usage_counts,
)


class OllamaProvider(BaseProvider):
    provider = "ollama"

    def __init__(self, api_key=None, base_url=None, options=None, *, transport=None):
        if api_key:
            raise provider_error("AUTH_INVALID", 422)
        self.base_url = local_base_url(base_url)
        super().__init__(None, self.base_url, options, transport=transport)

    def _request(self, method, path, *, options=None, body=None):
        with self._http_client(options) as client:
            response = client.request(method, self.base_url + path, json=body)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise provider_error("INVALID_RESPONSE")
            return response.json()

    def list_models(self) -> list[dict]:
        try:
            rows = self._request("GET", "/api/tags")["models"]
            if not isinstance(rows, list) or len(rows) > 1000:
                raise provider_error("INVALID_RESPONSE")
            result = []
            for row in rows:
                identifier = row.get("model") or row["name"]
                if not isinstance(identifier, str) or not identifier:
                    raise provider_error("INVALID_RESPONSE")
                result.append(model_record(self.provider, identifier))
            return result
        except Exception as exc:
            raise normalize_error(exc) from None

    def _generate(self, model_id, system, prompt, options, structured):
        native_options = {"num_predict": options["max_output_tokens"]}
        if "temperature" in options:
            native_options["temperature"] = options["temperature"]
        body = {"model": model_id, "stream": False, "options": native_options,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if structured:
            body["format"] = "json"
        response = self._request("POST", "/api/chat", options=options, body=body)
        if response.get("done") is not True or response.get("done_reason") != "stop":
            raise provider_error("INVALID_RESPONSE")
        if response["message"].get("tool_calls"):
            raise provider_error("INVALID_RESPONSE")
        usage = usage_counts(input_tokens=response.get("prompt_eval_count"), output_tokens=response.get("eval_count"))
        if len(usage) == 2:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        return {"content": response["message"]["content"], "usage": usage}
