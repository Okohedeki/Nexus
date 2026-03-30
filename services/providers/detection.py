"""Auto-detect installed providers and instantiate the configured one."""

import logging

from services.providers.claude_code import ClaudeCodeProvider
from services.providers.opencode import OpenCodeProvider
from services.providers.ollama import OllamaProvider

logger = logging.getLogger(__name__)

PROVIDERS = {
    "claude_code": ClaudeCodeProvider,
    "opencode": OpenCodeProvider,
    "ollama": OllamaProvider,
}

# Priority order for auto-detection
DETECTION_ORDER = ["claude_code", "opencode", "ollama"]


def detect_providers() -> dict[str, dict]:
    """Detect which providers are installed.

    Returns dict like:
    {
        "claude_code": {"available": True, "install": "npm install ..."},
        "opencode": {"available": False, "install": "brew install ..."},
        "ollama": {"available": True, "install": "..."},
    }
    """
    result = {}
    for name in DETECTION_ORDER:
        cls = PROVIDERS[name]
        result[name] = {
            "available": cls.is_available(),
            "install": cls.install_instructions(),
            "agentic": name != "ollama",
        }
    return result


def get_provider(provider_name: str = "", ollama_model: str = ""):
    """Get a provider instance by name, or auto-detect the best available one.

    Args:
        provider_name: Explicit provider name ("claude_code", "opencode", "ollama").
                       Empty string means auto-detect.
        ollama_model: Model to use with Ollama provider.

    Returns:
        Provider instance.

    Raises:
        RuntimeError: If no provider is available.
    """
    if provider_name:
        if provider_name not in PROVIDERS:
            raise RuntimeError(f"Unknown provider: {provider_name}. Options: {list(PROVIDERS.keys())}")

        cls = PROVIDERS[provider_name]
        if not cls.is_available():
            raise RuntimeError(
                f"Provider '{provider_name}' is not installed.\n{cls.install_instructions()}"
            )

        if provider_name == "ollama":
            return OllamaProvider(model=ollama_model)
        return cls()

    # Auto-detect
    for name in DETECTION_ORDER:
        cls = PROVIDERS[name]
        if cls.is_available():
            logger.info("Auto-detected provider: %s", name)
            if name == "ollama":
                return OllamaProvider(model=ollama_model)
            return cls()

    raise RuntimeError(
        "No LLM provider found. Install one of:\n"
        f"  Claude Code: {ClaudeCodeProvider.install_instructions()}\n"
        f"  OpenCode:    {OpenCodeProvider.install_instructions()}\n"
        f"  Ollama:      {OllamaProvider.install_instructions()}"
    )
