import os
from dataclasses import dataclass

from pagepatrol.config import LLMConfig, load_saved_key


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    # "anthropic" uses the Anthropic SDK, everything else speaks the OpenAI API.
    protocol: str
    base_url: str | None = None
    key_env: str | None = None
    needs_key: bool = True
    suggested_model: str | None = None


PROVIDERS = {
    p.id: p
    for p in [
        Provider(
            "anthropic",
            "Anthropic (Claude)",
            "anthropic",
            key_env="ANTHROPIC_API_KEY",
            suggested_model="claude-opus-5-5",
        ),
        Provider("openai", "OpenAI", "openai", key_env="OPENAI_API_KEY"),
        Provider(
            "gemini",
            "Google Gemini",
            "openai",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            key_env="GEMINI_API_KEY",
        ),
        Provider(
            "openrouter",
            "OpenRouter (many models, one key)",
            "openai",
            base_url="https://openrouter.ai/api/v1",
            key_env="OPENROUTER_API_KEY",
        ),
        Provider(
            "ollama",
            "Ollama (local models)",
            "openai",
            base_url="http://localhost:11434/v1",
            needs_key=False,
        ),
        Provider("custom", "Other OpenAI-compatible server", "openai", needs_key=False),
    ]
}


class LLMNotConfigured(RuntimeError):
    pass


def resolve_key(provider: Provider) -> str | None:
    """Environment wins over the saved key, so CI can inject one without touching files."""
    for name in ("PAGEPATROL_API_KEY", provider.key_env):
        if name and os.environ.get(name):
            return os.environ[name]
    return load_saved_key(provider.id)


def provider_for(config: LLMConfig) -> Provider:
    try:
        return PROVIDERS[config.provider]
    except KeyError:
        raise LLMNotConfigured(
            f"Unknown provider {config.provider!r} in config. Run: pagepatrol init"
        ) from None
