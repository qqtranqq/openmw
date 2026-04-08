"""API cost tracker — monitors spending and triggers graceful shutdown when budget is low."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Pricing per million tokens (as of 2025)
MODEL_PRICING = {
    # Sonnet
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    # Haiku
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    # Opus
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
}

# Default pricing if model not found
DEFAULT_PRICING = {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}


class CostTracker:
    """Tracks API costs across all calls."""

    def __init__(self, budget: float = 0.0, reserve: float = 10.0):
        """
        budget: Total budget in dollars (0 = unlimited)
        reserve: Dollars to reserve for graceful shutdown
        """
        self.budget = budget
        self.reserve = reserve
        self.total_cost = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_write_tokens = 0
        self.call_count = 0
        self._shutdown_triggered = False

    def record(self, response, model: str) -> float:
        """Record usage from an API response. Returns cost of this call."""
        usage = getattr(response, 'usage', None)
        if not usage:
            return 0.0

        input_tokens = getattr(usage, 'input_tokens', 0)
        output_tokens = getattr(usage, 'output_tokens', 0)
        cache_read = getattr(usage, 'cache_read_input_tokens', 0) or 0
        cache_write = getattr(usage, 'cache_creation_input_tokens', 0) or 0

        pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)

        cost = (
            (input_tokens / 1_000_000) * pricing["input"]
            + (output_tokens / 1_000_000) * pricing["output"]
            + (cache_read / 1_000_000) * pricing["cache_read"]
            + (cache_write / 1_000_000) * pricing["cache_write"]
        )

        self.total_cost += cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read
        self.total_cache_write_tokens += cache_write
        self.call_count += 1

        return cost

    @property
    def remaining(self) -> Optional[float]:
        """Remaining budget in dollars. None if unlimited."""
        if self.budget <= 0:
            return None
        return self.budget - self.total_cost

    @property
    def should_shutdown(self) -> bool:
        """True if remaining budget is at or below the reserve threshold."""
        if self.budget <= 0:
            return False
        return self.remaining <= self.reserve

    def check_and_warn(self) -> Optional[str]:
        """Returns a warning string if budget is low, None otherwise."""
        if self.budget <= 0:
            return None
        remaining = self.remaining
        if remaining <= self.reserve and not self._shutdown_triggered:
            self._shutdown_triggered = True
            return (
                f"API BUDGET LOW: ${remaining:.2f} remaining (reserve: ${self.reserve:.2f}). "
                f"Total spent: ${self.total_cost:.2f} across {self.call_count} calls."
            )
        return None

    def summary(self) -> str:
        """Return a cost summary string."""
        parts = [f"API Cost: ${self.total_cost:.2f}"]
        parts.append(f"Calls: {self.call_count}")
        parts.append(f"Tokens: {self.total_input_tokens:,} in / {self.total_output_tokens:,} out")
        if self.total_cache_read_tokens:
            parts.append(f"Cache: {self.total_cache_read_tokens:,} read / {self.total_cache_write_tokens:,} write")
        if self.budget > 0:
            parts.append(f"Remaining: ${self.remaining:.2f} / ${self.budget:.2f}")
        return " | ".join(parts)
