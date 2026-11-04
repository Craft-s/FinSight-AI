import importlib.util
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location("init_ai_key", Path(__file__).parents[2] / "scripts/init_ai_key.py")
key_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(key_setup)


def test_initialization_preserves_existing_config_and_never_prints_key(tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text("# Local configuration\nDATA_MODE=sec_snapshot\nMASTER_ENCRYPTION_KEY=\n", encoding="utf-8")
    message = key_setup.initialize(path)
    value = dotenv_values(path)["MASTER_ENCRYPTION_KEY"]
    assert Fernet(value.encode())
    assert value not in message
    assert "DATA_MODE=sec_snapshot" in path.read_text()
    before = path.read_bytes()
    assert "retained" in key_setup.initialize(path)
    assert path.read_bytes() == before
    assert capsys.readouterr().out == ""


def test_invalid_existing_key_is_not_overwritten(tmp_path):
    path = tmp_path / ".env"
    original = "MASTER_ENCRYPTION_KEY=invalid-fixture-key\n"
    path.write_text(original)
    with pytest.raises(ValueError, match="invalid encryption key"):
        key_setup.initialize(path)
    assert path.read_text() == original
