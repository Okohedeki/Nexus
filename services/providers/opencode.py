"""OpenCode CLI provider.

OpenCode is an open-source terminal-based AI coding agent (MIT license).
Supports 75+ LLM providers including OpenAI, Anthropic, Google, and local models via Ollama.
Note: The original opencode-ai/opencode repo is archived; development continues under Crush.
"""

import asyncio
import json
import logging
import shutil

logger = logging.getLogger(__name__)


class OpenCodeProvider:
    name = "opencode"
    supports_agentic = True
    supports_sessions = True

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
        cmd = ["opencode", "run", prompt]

        if model:
            cmd.extend(["--model", model])

        logger.info("OpenCode cmd: %s", " ".join(cmd[:4]) + "...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir or None,
        )

        accumulated_text = ""

        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # Try to parse as JSON event
                try:
                    event = json.loads(line)
                    event_type = event.get("type", "")

                    if event_type == "text":
                        text = event.get("content", event.get("text", ""))
                        accumulated_text += text
                        if on_text_delta:
                            await on_text_delta(text)
                    elif event_type in ("step_finish", "result"):
                        result_text = event.get("content", event.get("result", ""))
                        if result_text and not accumulated_text:
                            accumulated_text = result_text
                    continue
                except json.JSONDecodeError:
                    pass

                # Plain text output
                accumulated_text += line + "\n"
                if on_text_delta:
                    await on_text_delta(line + "\n")

            await proc.wait()

        except asyncio.CancelledError:
            proc.terminate()
            raise

        if on_result:
            await on_result(accumulated_text, 0.0)

        if not accumulated_text:
            stderr_data = await proc.stderr.read()
            err = stderr_data.decode("utf-8", errors="replace").strip()
            if err:
                accumulated_text = f"(No output)\n\nStderr:\n{err}"

        return accumulated_text, 0.0  # OpenCode doesn't report cost

    async def run_simple(
        self,
        prompt: str,
        *,
        model: str = "",
        working_dir: str = "",
        max_budget_usd: float = 1.0,
        timeout: int = 120,
    ) -> tuple[str, float]:
        cmd = ["opencode", "run", prompt]

        if model:
            cmd.extend(["--model", model])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir or None,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Timed out after {timeout}s", 0.0

        raw = stdout.decode("utf-8", errors="replace").strip()

        # Try to parse JSON output
        try:
            data = json.loads(raw)
            text = data.get("result", data.get("content", raw))
            return text, 0.0
        except json.JSONDecodeError:
            return raw or stderr.decode("utf-8", errors="replace").strip(), 0.0

    async def cancel(self, process) -> None:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

    @staticmethod
    def is_available() -> bool:
        return shutil.which("opencode") is not None

    @staticmethod
    def install_instructions() -> str:
        return (
            "Install OpenCode:\n"
            "  brew install opencode-ai/tap/opencode   (macOS)\n"
            "  go install github.com/opencode-ai/opencode@latest   (Go)\n"
            "  Or download from https://github.com/opencode-ai/opencode/releases"
        )
