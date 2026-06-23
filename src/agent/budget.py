"""RunBudget accounting and fail-closed termination checks."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from src.trace.models import CallCost, ModelUsage, RunBudget


class BudgetExceeded(RuntimeError):
    """A run cannot continue without exceeding its declared budget."""

    def __init__(self, limit: str, observed: int | Decimal, maximum: int | Decimal) -> None:
        self.limit = limit
        self.observed = observed
        self.maximum = maximum
        super().__init__(f"{limit} exceeded: observed={observed} maximum={maximum}")


@dataclass(frozen=True)
class BudgetSnapshot:
    iterations: int
    model_calls: int
    tool_calls: int
    input_tokens: int
    uncached_input_tokens: int
    peak_active_context_tokens: int
    output_tokens: int
    cost_usd: Decimal
    duration_ms: int


class BudgetTracker:
    def __init__(
        self,
        budget: RunBudget,
        *,
        max_iterations: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        self.budget = budget
        self.max_iterations = max_iterations
        self.clock = clock
        self.started_at = clock()
        self.iterations = 0
        self.model_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.uncached_input_tokens = 0
        self.peak_active_context_tokens = 0
        self.output_tokens = 0
        self.cost_usd = Decimal("0")

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            iterations=self.iterations,
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            input_tokens=self.input_tokens,
            uncached_input_tokens=self.uncached_input_tokens,
            peak_active_context_tokens=self.peak_active_context_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
            duration_ms=self.duration_ms,
        )

    @property
    def duration_ms(self) -> int:
        return max(0, round((self.clock() - self.started_at) * 1000))

    @property
    def remaining_output_tokens(self) -> int:
        return max(0, self.budget.max_output_tokens - self.output_tokens)

    def before_model_call(self) -> None:
        self._check_duration()
        if self.remaining_output_tokens < 1:
            raise BudgetExceeded(
                "max_output_tokens",
                self.output_tokens + 1,
                self.budget.max_output_tokens,
            )
        if self.iterations + 1 > self.max_iterations:
            raise BudgetExceeded(
                "max_iterations",
                self.iterations + 1,
                self.max_iterations,
            )
        if self.model_calls + 1 > self.budget.max_model_calls:
            raise BudgetExceeded(
                "max_model_calls",
                self.model_calls + 1,
                self.budget.max_model_calls,
            )
        self.iterations += 1
        self.model_calls += 1

    def after_model_call(self, usage: ModelUsage, cost: CallCost) -> None:
        self.input_tokens += usage.input_tokens
        self.uncached_input_tokens += usage.uncached_input_tokens
        self.peak_active_context_tokens = max(
            self.peak_active_context_tokens,
            usage.input_tokens,
        )
        self.output_tokens += usage.output_tokens
        self.cost_usd += cost.total_usd
        if self.budget.max_active_context_tokens is not None:
            self._check_limit(
                "max_active_context_tokens",
                usage.input_tokens,
                self.budget.max_active_context_tokens,
            )
        if self.budget.max_uncached_input_tokens is not None:
            self._check_limit(
                "max_uncached_input_tokens",
                self.uncached_input_tokens,
                self.budget.max_uncached_input_tokens,
            )
        if self.budget.max_input_tokens is not None:
            self._check_limit(
                "max_input_tokens",
                self.input_tokens,
                self.budget.max_input_tokens,
            )
        self._check_limit(
            "max_output_tokens",
            self.output_tokens,
            self.budget.max_output_tokens,
        )
        self._check_limit("max_cost_usd", self.cost_usd, self.budget.max_cost_usd)
        self._check_duration()

    def before_tool_calls(self, requested_calls: int) -> None:
        self._check_duration()
        if requested_calls < 1:
            raise ValueError("requested_calls must be at least 1")
        observed = self.tool_calls + requested_calls
        self._check_limit(
            "max_tool_calls",
            observed,
            self.budget.max_tool_calls,
        )

    def record_tool_call(self) -> None:
        self.tool_calls += 1
        self._check_duration()

    def check_duration(self) -> None:
        self._check_duration()

    def within_limits(self) -> bool:
        return (
            self.iterations <= self.max_iterations
            and self.model_calls <= self.budget.max_model_calls
            and self.tool_calls <= self.budget.max_tool_calls
            and (
                self.budget.max_active_context_tokens is None
                or self.peak_active_context_tokens
                <= self.budget.max_active_context_tokens
            )
            and (
                self.budget.max_uncached_input_tokens is None
                or self.uncached_input_tokens
                <= self.budget.max_uncached_input_tokens
            )
            and (
                self.budget.max_input_tokens is None
                or self.input_tokens <= self.budget.max_input_tokens
            )
            and self.output_tokens <= self.budget.max_output_tokens
            and self.cost_usd <= self.budget.max_cost_usd
            and self.duration_ms <= self.budget.max_duration_ms
        )

    def _check_duration(self) -> None:
        self._check_limit(
            "max_duration_ms",
            self.duration_ms,
            self.budget.max_duration_ms,
        )

    @staticmethod
    def _check_limit(
        name: str,
        observed: int | Decimal,
        maximum: int | Decimal,
    ) -> None:
        if observed > maximum:
            raise BudgetExceeded(name, observed, maximum)
