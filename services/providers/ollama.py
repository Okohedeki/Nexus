"""Ollama local LLM provider.

Uses the Ollama HTTP API for fully local, free inference.
No agentic features (no file editing, shell, MCP) — chat and entity extraction only.
"""

import asyncio
import json
import logging
import shutil

import httpx

logger = logging.getLogger(__name__)

OLLAMA_API = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2"


class OllamaProvider:
    name = "ollama"
    supports_agentic = False
    supports_sessions = False

    def __init__(self, model: str = ""):
        self._model = model or DEFAULT_MODEL

    async def run_streaming(
        self,
        prompt: str,
        *,
        model: str = "",
        working_dir: str = "",
        session_id: str = "",
        is_first_turn: bool = True,
        max_budget_usd: float = 1.0,
        on_text_delta=None,
        on_result=None,
    ) -> tuple[str, float]:
        use_model = model or self._model
        accumulated_text = ""

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_API}/api/generate",
                    json={"model": use_model, "prompt": prompt, "stream": True},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            text = data.get("response", "")
                            if text:
                                accumulated_text += text
                                if on_text_delta:
                                    await on_text_delta(text)
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue

        except httpx.ConnectError:
            accumulated_text = (
                "Error: Cannot connect to Ollama. Make sure it's running:\n"
                "  ollama serve"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                accumulated_text = (
                    f"Error: Model '{use_model}' not found. Pull it first:\n"
                    f"  ollama pull {use_model}"
                )
            else:
                accumulated_text = f"Error: Ollama returned HTTP {e.response.status_code}"
        except Exception as e:
            accumulated_text = f"Error: {e}"

        if on_result:
            await on_result(accumulated_text, 0.0)

        return accumulated_text, 0.0  # Local = free

    async def run_simple(
        self,
        prompt: str,
        *,
        model: str = "",
        working_dir: str = "",
        max_budget_usd: float = 1.0,
        timeout: int = 120,
    ) -> tuple[str, float]:
        use_model = model or self._model

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{OLLAMA_API}/api/generate",
                    json={"model": use_model, "prompt": prompt, "stream": False},
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", ""), 0.0

        except httpx.ConnectError:
            return "Error: Cannot connect to Ollama. Run: ollama serve", 0.0
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Error: Model '{use_model}' not found. Run: ollama pull {use_model}", 0.0
            return f"Error: Ollama HTTP {e.response.status_code}", 0.0
        except asyncio.TimeoutError:
            return f"Timed out after {timeout}s", 0.0
        except Exception as e:
            return f"Error: {e}", 0.0

    async def cancel(self, process) -> None:
        pass  # HTTP requests are cancelled by the client

    @staticmethod
    def is_available() -> bool:
        return shutil.which("ollama") is not None

    @staticmethod
    def install_instructions() -> str:
        return (
            "Install Ollama for local LLM inference:\n"
            "  Download from https://ollama.com/download\n"
            "  Then: ollama pull llama3.2"
        )
