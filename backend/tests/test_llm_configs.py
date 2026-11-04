"""Offline settings integration; provider doubles never enter production paths."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.api.llm import SaveConfig
from app.core.auth import require_owner
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decrypt_api_key, encrypt_api_key, encryption_ready, ollama_endpoint
from app.db.llm_models import LLMConfig
from app.db.session import session_factory
from app.services import llm_configs as service
from tests.conftest import TEST_TOKEN

HEADERS = {"X-Admin-Token": TEST_TOKEN}
PREFIX = "/api/v1/llm"
TEST_SECRET = "fixture-only-provider-secret-never-production"


class FakeProvider:
    """Contract double only for this test module."""

    def __init__(self):
        self.list_calls = 0
        self.test_calls = 0
        self.failure = None
        self.during_test = None
        self.during_list = None

    def validate_config(self, model_id):
        return {}

    def list_models(self):
        self.list_calls += 1
        if self.during_list:
            self.during_list()
        if self.failure:
            raise self.failure
        return [{"id": "fixture-model", "display_name": "Fixture test model", "provider": "openai",
                 "supports_text_generation": None, "context_window": None}]

    def test_connection(self, model_id):
        self.test_calls += 1
        if self.during_test:
            self.during_test()
        if self.failure:
            raise self.failure
        return {"usage": {"input_tokens": 4, "output_tokens": 2}, "duration_ms": 7}


@pytest.fixture
def llm_api(api, monkeypatch):
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    get_settings.cache_clear()
    service._models_cache.clear()
    fake = FakeProvider()
    monkeypatch.setattr(service, "create_provider", lambda *args, **kwargs: fake)
    yield api, fake
    service._models_cache.clear()


def save(api, **overrides):
    body = {"provider": "openai", "model_id": "fixture-model", "api_key": TEST_SECRET}
    body.update(overrides)
    response = api.post(f"{PREFIX}/configs", json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def verify(api, config):
    return api.post(f"{PREFIX}/configs/{config['id']}/test", json={"version": config["version"]}, headers=HEADERS)


def test_secret_encryption_is_authenticated_and_recipient_bound(llm_api, monkeypatch):
    ciphertext = encrypt_api_key(TEST_SECRET, "owner-1", "openai")
    assert TEST_SECRET not in ciphertext
    assert decrypt_api_key(ciphertext, "owner-1", "openai") == TEST_SECRET
    for owner, provider in [("owner-2", "openai"), ("owner-1", "deepseek")]:
        with pytest.raises(AppError, match="无法解密"):
            decrypt_api_key(ciphertext, owner, provider)
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(AppError) as exc:
        decrypt_api_key(ciphertext, "owner-1", "openai")
    assert exc.value.code == "CREDENTIAL_UNREADABLE"
    assert TEST_SECRET not in str(exc.value)


def test_configs_require_owner_and_never_return_plaintext_or_ciphertext(llm_api):
    api, _ = llm_api
    assert api.get(f"{PREFIX}/configs").status_code == 401
    assert api.post(f"{PREFIX}/configs", json={"provider": "openai"}).status_code == 401
    config = save(api)
    assert config["has_api_key"] is True
    assert config["is_verified"] is False
    assert config["is_active"] is False
    with session_factory()() as session:
        stored = session.get(LLMConfig, config["id"])
        ciphertext = stored.encrypted_key
        assert TEST_SECRET not in ciphertext
    listing = api.get(f"{PREFIX}/configs", headers=HEADERS)
    assert listing.status_code == 200
    assert TEST_SECRET not in listing.text
    assert ciphertext not in listing.text
    assert "encrypted_key" not in listing.text


@pytest.mark.parametrize("master", ["", "not-a-fernet-key", "无效密钥"])
def test_missing_or_invalid_master_does_not_disable_financial_browsing(llm_api, monkeypatch, master):
    api, _ = llm_api
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", master)
    get_settings.cache_clear()
    assert encryption_ready() is False
    assert api.get("/api/v1/companies/search").status_code == 200
    status = api.get(f"{PREFIX}/status")
    assert status.status_code == 200
    assert status.json()["encryption_ready"] is False
    response = api.post(f"{PREFIX}/configs", json={"provider": "openai", "api_key": TEST_SECRET}, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ENCRYPTION_NOT_CONFIGURED"
    assert TEST_SECRET not in response.text


def test_save_blank_model_then_list_manually_choose_test_and_activate(llm_api):
    api, fake = llm_api
    config = save(api, model_id="")
    assert verify(api, config).json()["error"]["code"] == "MODEL_REQUIRED"
    listed = api.post(f"{PREFIX}/configs/{config['id']}/models", json={}, headers=HEADERS)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == "fixture-model"
    config = save(api, id=config["id"], version=1, api_key=None, model_id="unlisted-manual-model")
    assert config["version"] == 2
    tested = verify(api, config)
    assert tested.status_code == 200, tested.text
    assert fake.test_calls == 1
    assert tested.json()["config"]["is_verified"] is True
    active = api.post(f"{PREFIX}/configs/{config['id']}/activate", json={"version": 2}, headers=HEADERS)
    assert active.status_code == 200, active.text
    assert active.json()["is_active"] is True
    assert api.get(f"{PREFIX}/status").json()["available"] is True


def test_models_cache_refresh_and_version_isolation(llm_api):
    api, fake = llm_api
    config = save(api)
    path = f"{PREFIX}/configs/{config['id']}/models"
    assert api.post(path, json={}, headers=HEADERS).json()["cached"] is False
    assert api.post(path, json={}, headers=HEADERS).json()["cached"] is True
    assert fake.list_calls == 1
    assert api.post(path, json={"refresh": True}, headers=HEADERS).json()["cached"] is False
    assert fake.list_calls == 2
    save(api, id=config["id"], version=1, api_key=None, model_id="next-fixture-model")
    assert api.post(path, json={}, headers=HEADERS).json()["cached"] is False
    assert fake.list_calls == 3


def test_optimistic_updates_invalidate_verification_and_activation(llm_api):
    api, _ = llm_api
    config = save(api)
    assert verify(api, config).status_code == 200
    assert api.post(f"{PREFIX}/configs/{config['id']}/activate", json={"version": 1}, headers=HEADERS).status_code == 200
    updated = save(api, id=config["id"], version=1, api_key=None, options={"max_output_tokens": 512})
    assert updated["version"] == 2
    assert updated["is_verified"] is False
    assert updated["verified_version"] is None
    assert updated["is_active"] is False
    stale = api.post(f"{PREFIX}/configs", json={"id": config["id"], "version": 1, "provider": "openai"}, headers=HEADERS)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONFIG_VERSION_CONFLICT"
    assert verify(api, config).status_code == 409


def test_duplicate_provider_and_provider_mutation_are_rejected(llm_api):
    api, _ = llm_api
    config = save(api)
    duplicate = api.post(f"{PREFIX}/configs", json={"provider": "openai", "api_key": TEST_SECRET}, headers=HEADERS)
    assert duplicate.status_code == 409
    mutation = api.post(f"{PREFIX}/configs", json={"id": config["id"], "version": 1, "provider": "deepseek"}, headers=HEADERS)
    assert mutation.status_code == 422
    missing_version = api.post(f"{PREFIX}/configs", json={"id": config["id"], "provider": "openai"}, headers=HEADERS)
    assert missing_version.status_code == 422


def test_owner_isolation_covers_list_models_test_activate_delete(llm_api):
    from app.main import app
    api, _ = llm_api
    config = save(api)
    app.dependency_overrides[require_owner] = lambda: "unrelated-test-owner"
    try:
        assert api.get(f"{PREFIX}/configs", headers=HEADERS).json()["items"] == []
        for action in ("models", "test", "activate", "delete"):
            body = {} if action == "models" else {"version": 1}
            response = api.post(f"{PREFIX}/configs/{config['id']}/{action}", json=body, headers=HEADERS)
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(require_owner)


def test_slow_test_cannot_validate_newly_changed_configuration(llm_api):
    api, fake = llm_api
    config = save(api)

    def change_config():
        with session_factory()() as session:
            service.save_config(session, "local-owner", SaveConfig(
                id=config["id"], version=1, provider="openai", model_id="changed-while-testing",
            ))

    fake.during_test = change_config
    response = verify(api, config)
    assert response.status_code == 409, response.text
    current = api.get(f"{PREFIX}/configs", headers=HEADERS).json()["items"][0]
    assert current["version"] == 2
    assert current["is_verified"] is False
    assert current["last_tested_at"] is None


def test_slow_model_list_cannot_cache_result_of_stale_revision(llm_api):
    api, fake = llm_api
    config = save(api)

    def change_config():
        with session_factory()() as session:
            service.save_config(session, "local-owner", SaveConfig(
                id=config["id"], version=1, provider="openai", model_id="changed-before-list-returned",
            ))

    fake.during_list = change_config
    response = api.post(f"{PREFIX}/configs/{config['id']}/models", json={}, headers=HEADERS)
    assert response.status_code == 409, response.text
    assert not service._models_cache


def test_failed_retest_disables_active_configuration_without_leaking_exception(llm_api):
    api, fake = llm_api
    config = save(api)
    assert verify(api, config).status_code == 200
    api.post(f"{PREFIX}/configs/{config['id']}/activate", json={"version": 1}, headers=HEADERS)
    fake.failure = RuntimeError(TEST_SECRET)
    response = verify(api, config)
    assert response.status_code == 503
    assert TEST_SECRET not in response.text
    stored = api.get(f"{PREFIX}/configs", headers=HEADERS).json()["items"][0]
    assert stored["is_verified"] is False
    assert stored["is_active"] is False
    assert stored["last_tested_at"]


def test_only_one_configuration_can_be_active_and_delete_is_version_guarded(llm_api):
    api, _ = llm_api
    first = save(api)
    second = save(api, provider="gemini")
    for config in (first, second):
        assert verify(api, config).status_code == 200
        assert api.post(f"{PREFIX}/configs/{config['id']}/activate", json={"version": 1}, headers=HEADERS).status_code == 200
    listing = api.get(f"{PREFIX}/configs", headers=HEADERS).json()["items"]
    assert [item["id"] for item in listing if item["is_active"]] == [second["id"]]
    assert api.post(f"{PREFIX}/configs/{second['id']}/delete", json={"version": 2}, headers=HEADERS).status_code == 409
    assert api.post(f"{PREFIX}/configs/{second['id']}/delete", json={"version": 1}, headers=HEADERS).json() == {"deleted": True}
    assert api.get(f"{PREFIX}/status").json()["available"] is False


@pytest.mark.parametrize("url", ["", "file:///tmp/key", "http://user:secret@localhost:11434", "http://169.254.169.254", "http://metadata.google.internal", "http://127.0.0.1:11434/api/chat", "http://127.0.0.1:11434?key=secret", "http://[fe80::1]:11434", "http://0.0.0.0:11434", "http://@localhost:11434", "http://[::ffff:169.254.169.254]:11434"])
def test_ollama_operator_url_rejects_unsafe_origins(llm_api, monkeypatch, url):
    monkeypatch.setenv("OLLAMA_BASE_URL", url)
    get_settings.cache_clear()
    with pytest.raises(AppError) as exc:
        ollama_endpoint()
    assert exc.value.code == "OLLAMA_NOT_CONFIGURED"


def test_ollama_uses_only_operator_origin_and_requires_no_cloud_key(llm_api, monkeypatch):
    api, _ = llm_api
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    config = save(api, provider="ollama", api_key=None)
    assert config["has_api_key"] is False
    assert verify(api, config).status_code == 200
    response = api.post(f"{PREFIX}/configs", json={"provider": "ollama", "base_url": "http://example.test"}, headers=HEADERS)
    assert response.status_code == 422
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    get_settings.cache_clear()
    current = api.get(f"{PREFIX}/configs", headers=HEADERS).json()["items"][0]
    assert current["is_verified"] is False


def test_master_rotation_invalidates_previous_verified_cloud_configuration(llm_api, monkeypatch):
    api, _ = llm_api
    config = save(api)
    assert verify(api, config).status_code == 200
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    current = api.get(f"{PREFIX}/configs", headers=HEADERS).json()["items"][0]
    assert current["is_verified"] is False
    response = api.post(f"{PREFIX}/configs/{config['id']}/models", json={}, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CREDENTIAL_UNREADABLE"


def test_invalid_options_and_key_input_do_not_echo_credentials(llm_api):
    api, _ = llm_api
    response = api.post(f"{PREFIX}/configs", headers=HEADERS, json={
        "provider": "openai", "api_key": TEST_SECRET, "options": {"timeout_seconds": TEST_SECRET},
    })
    assert response.status_code == 422
    assert TEST_SECRET not in response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    with session_factory()() as session:
        assert not session.scalars(select(LLMConfig)).all()
