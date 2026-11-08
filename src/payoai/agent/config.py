"""Model and thinking level for each LLM step. See ADR 0002. Recorded on every Trace Step."""

from dataclasses import dataclass

ARGON = "gemini-4-argon"
FLASH = "gemini-3.8-flash"
FLASH_LITE = "gemini-3.5-flash-lite"


@dataclass(frozen=True)
class StepConfig:
    model: str
    thinking_level: str
    timeout_seconds: int = 300
    retries: int = 1


STEPS = {
    "main_loop": StepConfig(FLASH, "low"),
    "extract_fields": StepConfig(FLASH, "medium"),
    "transcribe_document": StepConfig(FLASH, "low", timeout_seconds=120),
    "investigate": StepConfig(FLASH, "high", timeout_seconds=600),
    "explain": StepConfig(FLASH, "low", timeout_seconds=60),
    "suggest_category": StepConfig(FLASH, "low", timeout_seconds=60),
    "draft_request": StepConfig(FLASH, "low", timeout_seconds=60),
    "purchase_research": StepConfig(FLASH, "medium", timeout_seconds=600),
    "web_research": StepConfig(FLASH, "medium", timeout_seconds=300),
    "memory_learner": StepConfig(FLASH_LITE, "minimal", timeout_seconds=60),
    "judge": StepConfig(ARGON, "high", timeout_seconds=600),
}

# Standard prices in USD per million tokens, from Google's pricing page on 7 November 2026.
# Argon doubles on 1 December 2026 and 3.8 Flash on 1 January 2027.
PRICES = {
    ARGON: {"input": 2.00, "output": 10.00},
    FLASH: {"input": 0.75, "output": 3.75},
    FLASH_LITE: {"input": 0.30, "output": 2.50},
}


def cost_micros(model: str, input_tokens: int, output_tokens: int) -> int:
    price = PRICES[model]
    return round(input_tokens * price["input"] + output_tokens * price["output"])
