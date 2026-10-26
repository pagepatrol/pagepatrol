import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pagepatrol.config import LLMConfig
from pagepatrol.llm.providers import LLMNotConfigured, provider_for, resolve_key


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int | None, output_tokens: int | None) -> None:
        self.calls += 1
        self.input_tokens += input_tokens or 0
        self.output_tokens += output_tokens or 0


@dataclass
class Image:
    png: bytes
    label: str

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.png).decode("ascii")


@dataclass
class Request:
    system: str
    text: str
    images: list[Image] = field(default_factory=list)
    schema: dict[str, Any] | None = None
    schema_name: str = "result"
    effort: str = "medium"
    max_tokens: int = 16000


class Client(Protocol):
    model: str
    usage: Usage

    async def complete_json(self, request: Request) -> dict[str, Any]: ...

    async def list_models(self) -> list[str]: ...


def parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating code fences and chatter."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced[1] if fenced else text[text.find("{") : text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model didn't return valid JSON: {text[:200]!r}") from exc


def create_client(config: LLMConfig, api_key: str | None = None) -> Client:
    provider = provider_for(config)
    key = api_key or resolve_key(provider)
    if provider.needs_key and not key:
        raise LLMNotConfigured(
            f"No API key for {provider.label}. Run: pagepatrol init "
            f"(or set {provider.key_env or 'PAGEPATROL_API_KEY'})"
        )

    if provider.protocol == "anthropic":
        from pagepatrol.llm.anthropic import AnthropicClient

        return AnthropicClient(config.model, key, config.base_url)

    from pagepatrol.llm.openai import OpenAIClient

    base_url = config.base_url or provider.base_url
    return OpenAIClient(config.model, key or "not-needed", base_url)
