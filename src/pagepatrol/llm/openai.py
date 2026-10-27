from typing import Any

import openai

from pagepatrol.llm import LLMError, Request, Usage, parse_json


class OpenAIClient:
    """Any server that speaks the OpenAI chat completions API: OpenAI itself,
    Gemini, OpenRouter, Ollama, LM Studio, vLLM and so on."""

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self.usage = Usage()
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        # Not every compatible server supports strict JSON schemas. Remember
        # what worked so we don't pay for the failed attempt on every call.
        self._format_modes = ["json_schema", "json_object", "none"]

    def _messages(self, request: Request) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for image in request.images:
            content.append({"type": "text", "text": image.label})
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image.b64}"}}
            )
        content.append({"type": "text", "text": request.text})
        return [
            {"role": "system", "content": request.system},
            {"role": "user", "content": content},
        ]

    def _response_format(self, mode: str, request: Request) -> dict[str, Any] | None:
        if mode == "json_schema" and request.schema:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                },
            }
        if mode == "json_object":
            return {"type": "json_object"}
        return None

    async def complete_json(self, request: Request) -> dict[str, Any]:
        messages = self._messages(request)
        last_error: Exception | None = None
        for mode in list(self._format_modes):
            response_format = self._response_format(mode, request)
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **({"response_format": response_format} if response_format else {}),
                )
            except openai.BadRequestError as exc:
                # Most likely the server doesn't support this response_format.
                last_error = exc
                self._format_modes.remove(mode)
                continue
            except openai.AuthenticationError as exc:
                raise LLMError("The API key was rejected. Run: pagepatrol init") from exc
            except openai.NotFoundError as exc:
                raise LLMError(f"Model {self.model!r} not found.") from exc
            except openai.APIStatusError as exc:
                raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
            except openai.APIConnectionError as exc:
                raise LLMError("Couldn't reach the model server.") from exc

            if response.usage:
                self.usage.add(response.usage.prompt_tokens, response.usage.completion_tokens)
            else:
                self.usage.add(0, 0)
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise LLMError("The answer was cut off (hit the output limit).")
            return parse_json(choice.message.content or "")

        raise LLMError(f"The model server rejected the request: {last_error}")

    async def list_models(self) -> list[str]:
        try:
            return sorted([m.id async for m in self._client.models.list()])
        except openai.AuthenticationError as exc:
            raise LLMError("The API key was rejected.") from exc
        except openai.APIError as exc:
            raise LLMError(f"Couldn't list models: {exc}") from exc
