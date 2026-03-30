"""Claude Code CLI provider."""

import asyncio
import json
import logging
import shutil

logger = logging.getLogger(__name__)


class ClaudeCodeProvider:
    name = "claude_code"
    supports_agentic = True
    supports_sessions = True

    async def run_streaming(
        self,
        prompt: str,
        *,
        model: str = "sonnet",
        working_dir: str = "",
        session_id: str = "",
        is_first_turn: bool = True,
        max_budget_usd: float = 1.0,
        on_text_delta=None,
        on_result=None,
    ) -> tuple[str, float]:
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--model", model,
            "--max-budget-usd", str(max_budget_usd),
        ]

        if is_first_turn and session_id:
            cmd.extend(["--session-id", session_id])
        elif session_id:
            cmd.extend(["--resume", session_id])

        logger.info("Claude Code cmd: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir or None,
        )

        accumulated_text = ""
        cost_usd = 0.0

        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "assistant" and "message" in event:
                    content_blocks = event["message"].get("content", [])
                    for block in content_blocks:
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            accumulated_text += text
                            if on_text_delta:
                                await on_text_delta(text)

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        accumulated_text += text
                        if on_text_delta:
                            await on_text_delta(text)

                elif event_type == "result":
                    cost_usd = event.get("cost_usd", 0.0)
                    result_text = event.get("result", "")
                    if result_text and not accumulated_text:
                        accumulated_text = result_text
                    if on_result:
                        await on_result(accumulated_text, cost_usd)

            await proc.wait()

        except asyncio.CancelledError:
            proc.terminate()
            raise

        if not accumulated_text:
            stderr_data = await proc.stderr.read()
            err = stderr_data.decode("utf-8", errors="replace").strip()
            if err:
                accumulated_text = f"(No output)\n\nStderr:\n{err}"

        return accumulated_text, cost_usd

    async def run_simple(
        self,
        prompt: str,
        *,
        model: str = "sonnet",
        working_dir: str = "",
        max_budget_usd: float = 1.0,
        timeout: int = 120,
    ) -> tuple[str, float]:
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--model", model,
            "--max-budget-usd", str(max_budget_usd),
        ]

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
        cost_usd = 0.0

        try:
            data = json.loads(raw)
            text = data.get("result", raw)
            cost_usd = data.get("cost_usd", 0.0)
        except json.JSONDecodeError:
            text = raw or stderr.decode("utf-8", errors="replace").strip()

        return text, cost_usd

    async def cancel(self, process) -> None:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

    @staticmethod
    def is_available() -> bool:
        return shutil.which("claude") is not None

    @staticmethod
    def install_instructions() -> str:
        return "npm install -g @anthropic-ai/claude-code"
