import keyring
import pytest
from keyring.backends import fail

from pagepatrol.config import (
    LLMConfig,
    key_location,
    load_llm_config,
    load_saved_key,
    save_key,
    save_llm_config,
)
from pagepatrol.llm import create_client
from pagepatrol.llm.providers import PROVIDERS, LLMNotConfigured, resolve_key


def test_config_round_trip():
    assert load_llm_config() is None
    save_llm_config(LLMConfig("ollama", "llava", base_url="http://localhost:11434/v1"))
    assert load_llm_config() == LLMConfig("ollama", "llava", "http://localhost:11434/v1")


def test_keys_go_to_the_keyring(isolated_home):
    assert save_key("anthropic", "secret") == "system keyring"
    assert load_saved_key("anthropic") == "secret"
    assert key_location("anthropic") == "system keyring"
    assert not (isolated_home / "credentials.toml").exists()


def test_falls_back_to_a_file_without_a_keyring(isolated_home):
    keyring.set_keyring(fail.Keyring())
    where = save_key("anthropic", 'with-"quotes"')
    assert where.endswith("credentials.toml")
    assert load_saved_key("anthropic") == 'with-"quotes"'
    assert key_location("anthropic") == where


def test_moving_to_the_keyring_removes_the_plain_text_copy(isolated_home):
    working = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    save_key("anthropic", "old")
    keyring.set_keyring(working)

    save_key("anthropic", "new")
    assert load_saved_key("anthropic") == "new"
    assert not (isolated_home / "credentials.toml").exists()


def test_key_resolution_prefers_environment(monkeypatch):
    provider = PROVIDERS["anthropic"]
    assert resolve_key(provider) is None

    save_key("anthropic", "saved")
    assert resolve_key(provider) == "saved"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert resolve_key(provider) == "from-env"
    monkeypatch.setenv("PAGEPATROL_API_KEY", "override")
    assert resolve_key(provider) == "override"


def test_missing_key_is_a_clear_error():
    with pytest.raises(LLMNotConfigured, match="pagepatrol init"):
        create_client(LLMConfig("openai", "some-model"))


def test_local_providers_need_no_key():
    client = create_client(LLMConfig("ollama", "llava"))
    assert client.model == "llava"


def test_unknown_provider():
    with pytest.raises(LLMNotConfigured):
        create_client(LLMConfig("nope", "x"))


def test_prices():
    from pagepatrol.llm.pricing import estimate_cost, prices_for

    assert prices_for(LLMConfig("anthropic", "claude-sonnet-5-5")) == (2.0, 10.0)
    assert prices_for(LLMConfig("anthropic", "claude-unknown")) is None
    assert prices_for(LLMConfig("openai", "some-model")) is None
    assert prices_for(LLMConfig("ollama", "llava")) == (0.0, 0.0)
    custom = LLMConfig("openai", "some-model", price_input=1.5, price_output=6.0)
    assert prices_for(custom) == (1.5, 6.0)

    save_llm_config(custom)
    assert load_llm_config() == custom
    assert estimate_cost((1.5, 6.0), 2_000_000, 100_000) == pytest.approx(3.6)
    assert estimate_cost(None, 1, 1) is None
