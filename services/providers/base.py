"""Base provider protocol for LLM CLI backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Interface that all LLM providers must implement."""

    name: str
    supports_agentic: bool  # Can edit files, run shell, use MCP?
    supports_sessions: bool  # Can resume multi-turn conversations?

    async def run_streaming(
        self,
        prompt: str,
        *,
        model: str = "",
        working_dir: str = "",
        session_id: str = "",
        is_first_turn: bool = True,
        max_budget_usd: float = 1.0,
        on_text_delta: callable = None,
        on_result: callable = None,
    ) -> tuple[str, float]:
        """Run a prompt with streaming output.

        Returns (full_text, cost_usd).
        Calls on_text_delta(chunk) for each text chunk.
        Calls on_result(full_text, cost_usd) when complete.
        """
        ...

    async def run_simple(
        self,
        prompt: str,
        *,
        model: str = "",
        working_dir: str = "",
        max_budget_usd: float = 1.0,
        timeout: int = 120,
    ) -> tuple[str, float]:
        """Run a one-shot prompt. Returns (text, cost_usd)."""
        ...

    async def cancel(self, process) -> None:
        """Kill a running process."""
        ...

    @staticmethod
    def is_available() -> bool:
        """Check if this provider's CLI is installed."""
        ...

    @staticmethod
    def install_instructions() -> str:
        """Return human-readable install instructions."""
        ...
