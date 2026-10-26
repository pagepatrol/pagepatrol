import pytest

from pagepatrol.config import LLMConfig, load_llm_config, load_saved_key, save_key, save_llm_config
from pagepatrol.llm import create_client
from pagepatrol.llm.providers import PROVIDERS, LLMNotConfigured, resolve_key


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PAGEPATROL_HOME", str(tmp_path))
    for name in ("PAGEPATROL_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_config_round_trip():
    assert load_llm_config() is None
    save_llm_config(LLMConfig("ollama", "llava", base_url="http://localhost:11434/v1"))
    assert load_llm_config() == LLMConfig("ollama", "llava", "http://localhost:11434/v1")


def test_key_resolution_prefers_environment(monkeypatch):
    provider = PROVIDERS["anthropic"]
    assert resolve_key(provider) is None

    save_key("anthropic", 'saved-"key"')
    assert load_saved_key("anthropic") == 'saved-"key"'
    assert resolve_key(provider) == 'saved-"key"'

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
