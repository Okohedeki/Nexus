"""Session management and LLM execution using pluggable providers."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from services.providers.detection import get_provider

logger = logging.getLogger(__name__)

# Per-user sessions keyed by user_id (e.g. "telegram:123", "discord:456")
_sessions: dict[str, "Session"] = {}

# Cached provider instance
_provider = None


def _get_provider(config):
    """Get or create the provider instance from config."""
    global _provider
    if _provider is None:
        provider_name = getattr(config, "provider", "") or ""
        ollama_model = getattr(config, "ollama_model", "") or ""
        _provider = get_provider(provider_name, ollama_model)
        logger.info("Using provider: %s", _provider.name)
    return _provider


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    process: asyncio.subprocess.Process | None = None
    working_dir: str = ""
    model: str = "sonnet"
    total_cost_usd: float = 0.0
    is_first_turn: bool = True


def get_session(user_id: str, default_cwd: str, default_model: str) -> Session:
    """Get or create a session for a user."""
    if user_id not in _sessions:
        _sessions[user_id] = Session(
            working_dir=default_cwd, model=default_model
        )
    return _sessions[user_id]


def reset_session(user_id: str, default_cwd: str, default_model: str) -> Session:
    """Create a fresh session for a user."""
    _sessions[user_id] = Session(
        working_dir=default_cwd, model=default_model
    )
    return _sessions[user_id]


async def cancel_claude(user_id: str) -> bool:
    """Kill the running process for a user. Returns True if killed."""
    session = _sessions.get(user_id)
    if session and session.process and session.process.returncode is None:
        session.process.terminate()
        try:
            await asyncio.wait_for(session.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            session.process.kill()
        session.process = None
        return True
    return False


def is_running(user_id: str) -> bool:
    session = _sessions.get(user_id)
    return bool(
        session and session.process and session.process.returncode is None
    )


async def run_claude_streaming(
    user_id: str,
    prompt: str,
    config,
    on_text_delta: callable = None,
    on_result: callable = None,
):
    """Run a prompt with streaming output via the configured provider.

    Calls on_text_delta(text) for each content chunk.
    Calls on_result(full_text, cost_usd) when complete.
    """
    provider = _get_provider(config)
    session = get_session(user_id, config.default_cwd, config.default_model)

    if is_running(user_id):
        raise RuntimeError("Already running for this chat")

    original_on_result = on_result

    async def wrapped_on_result(full_text, cost_usd):
        session.total_cost_usd += cost_usd
        if original_on_result:
            await original_on_result(full_text, cost_usd)

    text, cost_usd = await provider.run_streaming(
        prompt,
        model=session.model,
        working_dir=session.working_dir,
        session_id=session.session_id if provider.supports_sessions else "",
        is_first_turn=session.is_first_turn,
        max_budget_usd=config.max_budget_usd,
        on_text_delta=on_text_delta,
        on_result=wrapped_on_result,
    )

    session.is_first_turn = False
    return text, cost_usd


async def run_claude_simple(
    user_id: str, prompt: str, config
) -> tuple[str, float]:
    """Non-streaming one-shot. Returns (text, cost_usd)."""
    provider = _get_provider(config)
    session = get_session(user_id, config.default_cwd, config.default_model)

    if is_running(user_id):
        raise RuntimeError("Already running for this chat")

    text, cost_usd = await provider.run_simple(
        prompt,
        model=session.model,
        working_dir=session.working_dir,
        max_budget_usd=config.max_budget_usd,
        timeout=config.claude_timeout,
    )

    session.total_cost_usd += cost_usd
    session.is_first_turn = False
    return text, cost_usd
