import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class PlatformConfig:
    enabled: bool
    token: str
    allowed_ids: frozenset[str]


@dataclass(frozen=True)
class Config:
    telegram: PlatformConfig | None
    discord: PlatformConfig | None
    provider: str  # "claude_code", "opencode", "ollama", or "" for auto-detect
    ollama_model: str
    default_cwd: str
    default_model: str
    claude_timeout: int
    shell_timeout: int
    max_budget_usd: float
    kg_db_path: str
    whisper_model: str


def _load_platform(token_var: str, ids_var: str, legacy_ids_var: str = None) -> PlatformConfig | None:
    """Load a platform config from environment variables."""
    token = os.environ.get(token_var, "").strip()
    if not token or token.startswith("your-"):
        return None

    raw_ids = os.environ.get(ids_var, "")
    # Support legacy variable name
    if not raw_ids and legacy_ids_var:
        raw_ids = os.environ.get(legacy_ids_var, "")

    allowed = frozenset(
        cid.strip() for cid in raw_ids.split(",") if cid.strip()
    )

    return PlatformConfig(enabled=True, token=token, allowed_ids=allowed)


def load_config() -> Config:
    telegram = _load_platform(
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_IDS",
        legacy_ids_var="ALLOWED_CHAT_IDS",
    )
    discord = _load_platform(
        "DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_IDS",
    )

    return Config(
        telegram=telegram,
        discord=discord,
        provider=os.environ.get("PROVIDER", ""),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
        default_cwd=os.environ.get("DEFAULT_CWD", os.getcwd()),
        default_model=os.environ.get("DEFAULT_MODEL", "sonnet"),
        claude_timeout=int(os.environ.get("CLAUDE_TIMEOUT", "300")),
        shell_timeout=int(os.environ.get("SHELL_TIMEOUT", "60")),
        max_budget_usd=float(os.environ.get("MAX_BUDGET_USD", "1.0")),
        kg_db_path=os.environ.get(
            "KG_DB_PATH", os.path.join(os.getcwd(), "data", "knowledge.db")
        ),
        whisper_model=os.environ.get("WHISPER_MODEL", "base"),
    )
