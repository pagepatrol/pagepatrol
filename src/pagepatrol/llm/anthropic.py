import re
from typing import Any

import anthropic

from pagepatrol.llm import LLMError, Request, Usage, parse_json

# Models that take output_config.effort. Older ones reject it.
_EFFORT_MODELS = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-(5|4-[678])")

# On these, a safety-classifier refusal can be retried server-side on another
# model instead of failing the whole review.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient:
    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self.usage = Usage()
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)
        self._first_party = base_url is None

    async def complete_json(self, request: Request) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for image in request.images:
            content.append({"type": "text", "text": image.label})
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": image.b64},
                }
            )
        content.append({"type": "text", "text": request.text})

        output_config: dict[str, Any] = {}
        if request.schema:
            output_config["format"] = {"type": "json_schema", "schema": request.schema}
        if _EFFORT_MODELS.match(self.model):
            output_config["effort"] = request.effort

        extra: dict[str, Any] = {}
        if self._first_party and self.model in _FALLBACK_MODELS:
            extra = {
                "extra_headers": {"anthropic-beta": _FALLBACK_BETA},
                "extra_body": {"fallbacks": "default"},
            }

        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": content}],
        }
        if output_config:
            params["output_config"] = output_config

        try:
            try:
                response = await self._client.messages.create(**params, **extra)
            except anthropic.BadRequestError:
                if not extra:
                    raise
                # Fallbacks are a beta; if this account can't use them, go without.
                response = await self._client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key. Run: pagepatrol init") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Model {self.model!r} not found on Anthropic.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Couldn't reach the Anthropic API.") from exc

        self.usage.add(response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise LLMError(
                "The model declined the request" + (f" ({category})." if category else ".")
            )
        if response.stop_reason == "max_tokens":
            raise LLMError("The answer was cut off (hit max_tokens).")
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_json(text)

    async def list_models(self) -> list[str]:
        try:
            return [m.id async for m in self._client.models.list()]
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key.") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Couldn't list models: {exc}") from exc
