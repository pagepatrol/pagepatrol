from pagepatrol.config import LLMConfig

# USD per million tokens (input, output), Anthropic list prices as of 2026-10.
# Thinking tokens are billed as output and are already included in the
# output count the API reports. Other providers change prices too often to
# hardcode; users can set them in `pagepatrol init`.
PRICES_AS_OF = "2026-10"
KNOWN_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}

FREE_PROVIDERS = {"ollama"}


def prices_for(config: LLMConfig) -> tuple[float, float] | None:
    if config.price_input is not None and config.price_output is not None:
        return config.price_input, config.price_output
    if config.provider in FREE_PROVIDERS:
        return 0.0, 0.0
    if config.provider == "anthropic":
        return KNOWN_PRICES.get(config.model)
    return None


def estimate_cost(
    prices: tuple[float, float] | None, input_tokens: int, output_tokens: int
) -> float | None:
    if prices is None:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
