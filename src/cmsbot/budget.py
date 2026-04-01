"""Token and cost budget tracking with per-model pricing."""

from __future__ import annotations

from dataclasses import dataclass, field

# Prices per 1M tokens (USD) — update as pricing changes
MODEL_COSTS: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    # OpenAI
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # Gemini
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    # Qwen via OpenRouter
    "qwen/qwen3.5-397b-a17b": {"input": 0.39, "output": 2.34},
    "Qwen/Qwen3-235B-A22B": {"input": 0.20, "output": 0.60},
}

# Fallback cost if model not in table
FALLBACK_COST = {"input": 3.00, "output": 15.00}

# DALL-E pricing per image
DALLE_COSTS = {
    "1024x1024": 0.040,
    "1024x1792": 0.080,
    "1792x1024": 0.080,
}


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute USD cost for a given model and token counts."""
    costs = MODEL_COSTS.get(model, FALLBACK_COST)
    return (
        input_tokens * costs["input"] / 1_000_000
        + output_tokens * costs["output"] / 1_000_000
    )


@dataclass
class BudgetEntry:
    agent: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float


@dataclass
class BudgetTracker:
    """Tracks spending across the pipeline and enforces a cap."""

    cap: float = 75.0
    entries: list[BudgetEntry] = field(default_factory=list)
    image_cost: float = 0.0

    @property
    def spent(self) -> float:
        return sum(e.cost for e in self.entries) + self.image_cost

    @property
    def remaining(self) -> float:
        return max(0.0, self.cap - self.spent)

    @property
    def utilization(self) -> float:
        return self.spent / self.cap if self.cap > 0 else 1.0

    def record(
        self,
        agent: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> BudgetEntry:
        cost = compute_cost(model, input_tokens, output_tokens)
        entry = BudgetEntry(
            agent=agent,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        self.entries.append(entry)
        return entry

    def record_image(self, size: str = "1024x1024") -> float:
        cost = DALLE_COSTS.get(size, 0.04)
        self.image_cost += cost
        return cost

    def can_afford(self, estimated_cost: float) -> bool:
        return self.spent + estimated_cost <= self.cap

    def should_downgrade(self) -> bool:
        """Return True if budget is running low and we should use cheaper models."""
        return self.utilization > 0.6

    def summary(self) -> dict:
        total_input = sum(e.input_tokens for e in self.entries)
        total_output = sum(e.output_tokens for e in self.entries)
        return {
            "total_cost": round(self.spent, 4),
            "budget_cap": self.cap,
            "remaining": round(self.remaining, 4),
            "utilization": f"{self.utilization:.1%}",
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "image_cost": round(self.image_cost, 4),
            "num_api_calls": len(self.entries),
        }
