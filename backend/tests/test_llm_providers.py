"""Offline provider protocol fixtures: these are not live model responses."""

import json

import httpx
import pytest

from app.core.errors import AppError
from app.services.llm.base import OriginTransport, usage_counts
from app.services.llm.factory import create_provider

PROVIDERS = ("openai", "deepseek", "gemini", "ollama")
TEST_KEY = "synthetic-test-api-key-not-a-real-credential"


def provider(name, handler, options=None):
    return create_provider(
        name, api_key=TEST_KEY if name != "ollama" else None,
        base_url="http://127.0.0.1:11434" if name == "ollama" else None,
        options=options, transport=httpx.MockTransport(handler),
    )


def generated(name, content='{"result":"fixture"}', finish="stop"):
    if name == "openai":
        return {"id": "resp_fixture", "object": "response", "created_at": 1,
                "model": "manual-model", "status": "completed" if finish == "stop" else "incomplete",
                "output": [{"type": "message", "id": "msg_fixture", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": content, "annotations": []}]}],
                "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}}
    if name == "gemini":
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": content}]},
                                "finishReason": "STOP" if finish == "stop" else "MAX_TOKENS"}],
                "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10}}
    if name == "deepseek":
        return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}
    return {"message": {"role": "assistant", "content": content}, "done": True,
            "done_reason": finish, "prompt_eval_count": 7, "eval_count": 3}


@pytest.mark.parametrize("name", PROVIDERS)
def test_actual_sdk_or_http_protocol_generation(name):
    requests = []

    def respond(request):
        requests.append(request)
        assert TEST_KEY not in str(request.url)
        return httpx.Response(200, json=generated(name))

    result = provider(name, respond).generate_structured("manual-model", "System rules", "Evidence")
    assert json.loads(result["content"]) == {"result": "fixture"}
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    assert result["duration_ms"] >= 0
    assert len(requests) == 1
    request = requests[0]
    body = json.loads(request.content)
    assert body["model"] == "manual-model" if name != "gemini" else request.url.path.endswith("/manual-model:generateContent")
    if name == "openai":
        assert request.url.path == "/v1/responses"
        assert body["max_output_tokens"] == 2048 and body["store"] is False
        assert "reasoning" not in body and "temperature" not in body
        assert request.headers["authorization"] == f"Bearer {TEST_KEY}"
    elif name == "deepseek":
        assert request.url.path == "/chat/completions"
        assert body["response_format"] == {"type": "json_object"} and body["max_tokens"] == 2048
        assert "reasoning_effort" not in body and "temperature" not in body
    elif name == "gemini":
        assert body["generationConfig"]["maxOutputTokens"] == 2048
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert request.headers["x-goog-api-key"] == TEST_KEY
    else:
        assert request.url.path == "/api/chat"
        assert body["options"] == {"num_predict": 2048} and body["format"] == "json"
        assert "authorization" not in request.headers


@pytest.mark.parametrize("name", PROVIDERS)
def test_connection_uses_real_short_text_request_and_safe_metadata(name):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=generated(name, "OK"))

    result = provider(name, respond).test_connection("manual-model")
    assert result["model_id"] == "manual-model" and "content" not in result
    assert result["usage"]["total_tokens"] == 10
    assert len(requests) == 1
    body = requests[0]
    if name == "openai":
        assert body["input"] == "Reply OK." and body["max_output_tokens"] == 512
    elif name == "gemini":
        assert body["generationConfig"]["maxOutputTokens"] == 512
        assert "responseMimeType" not in body["generationConfig"]
    elif name == "ollama":
        assert body["options"]["num_predict"] == 512 and "format" not in body
    else:
        assert body["max_tokens"] == 512 and "response_format" not in body


@pytest.mark.parametrize("name", PROVIDERS)
@pytest.mark.parametrize("content", ["", "not json", "[]", '{"x":NaN}', "```json\n{}\n```"])
def test_invalid_structured_output_never_accepted(name, content):
    with pytest.raises(AppError) as error:
        provider(name, lambda _: httpx.Response(200, json=generated(name, content))).generate_structured("manual-model", "", "")
    assert error.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("name", PROVIDERS)
def test_truncated_output_rejected_even_if_json_is_valid(name):
    with pytest.raises(AppError) as error:
        provider(name, lambda _: httpx.Response(200, json=generated(name, "{}", finish="length"))).generate_structured("manual-model", "", "")
    assert error.value.code == "INVALID_RESPONSE"


@pytest.mark.parametrize("name", PROVIDERS)
@pytest.mark.parametrize("status,code", [(401, "AUTH_INVALID"), (403, "AUTH_INVALID"), (404, "MODEL_NOT_FOUND"),
                                       (429, "RATE_LIMITED"), (500, "PROVIDER_UNAVAILABLE"),
                                       (400, "MODEL_NOT_SUPPORTED"), (504, "TIMEOUT")])
def test_error_mapping_omits_keys_upstream_text_and_has_no_automatic_retries(name, status, code):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"code": status, "message": TEST_KEY, "status": "UNKNOWN"}})

    with pytest.raises(AppError) as error:
        provider(name, respond).test_connection("manual-model")
    assert error.value.code == code
    assert TEST_KEY not in str(error.value) and TEST_KEY not in str(error.value.details)
    assert len(requests) == 1


@pytest.mark.parametrize("name", PROVIDERS)
def test_timeout_normalization(name):
    def respond(request):
        raise httpx.ReadTimeout(TEST_KEY, request=request)

    with pytest.raises(AppError) as error:
        provider(name, respond).test_connection("manual-model")
    assert error.value.code == "TIMEOUT" and TEST_KEY not in str(error.value)


@pytest.mark.parametrize("name", PROVIDERS)
def test_redirect_is_never_followed(name):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(AppError):
        provider(name, respond).list_models()
    assert len(requests) == 1


def test_openai_list_keeps_unknown_capabilities_unknown():
    model = provider("openai", lambda _: httpx.Response(200, json={"object": "list", "data": [
        {"id": "manual-model", "object": "model", "created": 1, "owned_by": "fixture"}]})).list_models()[0]
    assert model == {"id": "manual-model", "display_name": "manual-model", "provider": "openai",
                     "supports_text_generation": None, "context_window": None}


def test_deepseek_models_optional_declared_metadata_and_missing_fields():
    rows = [{"id": "model-a", "name": "Model A", "context_window": 10000, "output_modalities": ["text"]},
            {"id": "model-b"}]
    models = provider("deepseek", lambda _: httpx.Response(200, json={"data": rows})).list_models()
    assert models[0]["supports_text_generation"] is True and models[0]["context_window"] == 10000
    assert models[0]["display_name"] == "Model A"
    assert models[1]["supports_text_generation"] is None and models[1]["context_window"] is None


def test_gemini_sdk_pagination_and_method_filtering():
    requests = []

    def respond(request):
        requests.append(request)
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"models": [
                {"name": "models/model-a", "displayName": "A", "inputTokenLimit": 1234,
                 "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed-a", "supportedGenerationMethods": ["embedContent"]}], "nextPageToken": "page-2"})
        assert request.url.params["pageToken"] == "page-2"
        return httpx.Response(200, json={"models": [{"name": "models/model-b", "supportedGenerationMethods": ["generateContent"]}]})

    models = provider("gemini", respond).list_models()
    assert [m["id"] for m in models] == ["model-a", "model-b"]
    assert models[0]["context_window"] == 1234
    assert all(m["supports_text_generation"] is True for m in models)
    assert len(requests) == 2


def test_ollama_tags_are_installed_models_with_unknown_generation_capabilities():
    models = provider("ollama", lambda _: httpx.Response(200, json={"models": [{"name": "local:tag", "model": "local:tag"}]})).list_models()
    assert models[0]["id"] == "local:tag" and models[0]["supports_text_generation"] is None


@pytest.mark.parametrize("name", PROVIDERS)
def test_empty_model_catalog_and_malformed_response(name):
    key = "models" if name in {"gemini", "ollama"} else "data"
    assert provider(name, lambda _: httpx.Response(200, json={key: []})).list_models() == []
    with pytest.raises(AppError):
        provider(name, lambda _: httpx.Response(200, content=b"broken JSON")).list_models()


@pytest.mark.parametrize("name", PROVIDERS)
def test_input_budget_and_model_identifier_fail_before_network(name):
    requests = []
    adapter = provider(name, lambda request: requests.append(request))
    with pytest.raises(AppError) as error:
        adapter.generate_structured("manual-model", "a" * 24_000, "b")
    assert error.value.code == "CONTEXT_TOO_LARGE"
    with pytest.raises(AppError):
        adapter.test_connection("../../metadata")
    assert not requests


def test_openai_explicit_advanced_parameters_use_responses_protocol():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=generated("openai"))

    provider("openai", respond, {"reasoning_effort": "low", "temperature": 0.2}).generate_structured("unknown-new-id", "", "")
    assert requests[0]["reasoning"] == {"effort": "low"} and requests[0]["temperature"] == 0.2


def test_deepseek_temperature_requires_explicit_non_thinking_mode():
    adapter = provider("deepseek", lambda _: httpx.Response(200, json=generated("deepseek")), {"temperature": 0.2})
    with pytest.raises(AppError):
        adapter.validate_config("manual-model")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=generated("deepseek"))

    provider("deepseek", respond, {"temperature": 0.2, "reasoning_effort": "none"}).generate_structured("manual-model", "", "")
    assert requests[0]["reasoning_effort"] == "none" and requests[0]["temperature"] == 0.2


@pytest.mark.parametrize("name", ["gemini", "ollama"])
def test_non_portable_reasoning_option_is_rejected(name):
    with pytest.raises(AppError):
        provider(name, lambda _: None, {"reasoning_effort": "high"}).validate_config("manual-model")


@pytest.mark.parametrize("options", [{"max_output_tokens": True}, {"max_output_tokens": 0}, {"max_output_tokens": 8193},
                                   {"timeout_seconds": float("nan")}, {"timeout_seconds": 121},
                                   {"temperature": float("inf")}, {"temperature": -1}, {"arbitrary_field": "x"}])
def test_invalid_options_rejected(options):
    with pytest.raises(AppError):
        provider("openai", lambda _: None, options)


def test_cloud_base_url_override_is_forbidden_and_transport_pins_origin():
    with pytest.raises(AppError):
        create_provider("openai", TEST_KEY, "https://attacker.invalid/v1")
    with pytest.raises(AppError):
        create_provider("gemini", TEST_KEY, "http://127.0.0.1:1234")
    transport = OriginTransport("https://api.openai.com/v1", httpx.MockTransport(lambda _: httpx.Response(200)))
    with pytest.raises(AppError):
        transport.handle_request(httpx.Request("GET", "https://api.openai.com/other"))
    with pytest.raises(AppError):
        transport.handle_request(httpx.Request("GET", "http://169.254.169.254/v1/models"))


@pytest.mark.parametrize("url", [None, "", "file:///etc/passwd", "http://user:pass@localhost:11434", "http://localhost:11434/path", "http://localhost:11434?x=y"])
def test_ollama_url_shape_rejected(url):
    with pytest.raises(AppError):
        create_provider("ollama", base_url=url)


def test_provider_and_auth_validation_and_usage_whitelist():
    with pytest.raises(AppError):
        create_provider("fake")
    with pytest.raises(AppError):
        create_provider("openai")
    with pytest.raises(AppError):
        create_provider("ollama", api_key=TEST_KEY, base_url="http://localhost:11434")
    assert usage_counts(input_tokens=True, output_tokens=-1, total_tokens=None) == {}


def test_openai_explicit_refusal_never_passes_as_text():
    body = generated("openai")
    body["output"][0]["content"] = [{"type": "refusal", "refusal": TEST_KEY}]
    with pytest.raises(AppError) as error:
        provider("openai", lambda _: httpx.Response(200, json=body)).test_connection("manual-model")
    assert error.value.code == "INVALID_RESPONSE" and TEST_KEY not in str(error.value)


def test_gemini_safety_block_never_passes_as_valid_generation():
    body = generated("gemini")
    body["candidates"][0]["finishReason"] = "SAFETY"
    with pytest.raises(AppError) as error:
        provider("gemini", lambda _: httpx.Response(200, json=body)).test_connection("manual-model")
    assert error.value.code == "INVALID_RESPONSE"


def test_gemini_structured_api_key_error_is_auth_error_without_echo():
    body = {"error": {"code": 400, "message": TEST_KEY, "status": "INVALID_ARGUMENT",
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}}
    with pytest.raises(AppError) as error:
        provider("gemini", lambda _: httpx.Response(400, json=body)).test_connection("manual-model")
    assert error.value.code == "AUTH_INVALID" and TEST_KEY not in str(error.value)


def test_openai_per_call_timeout_and_options_do_not_mutate_shared_configuration():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=generated("openai"))

    adapter = provider("openai", respond)
    adapter.generate_structured("manual-model", "", "", {"timeout_seconds": 45, "max_output_tokens": 1024})
    assert requests[0].extensions["timeout"]["read"] == 45
    assert adapter.options == {"max_output_tokens": 2048, "timeout_seconds": 30}
