"""Direct HTTP delivery of digests to Telegram/Discord."""

import logging

import httpx

from services.output_formatter import chunk_message

logger = logging.getLogger(__name__)


async def deliver_to_telegram(content: str, token: str, chat_ids: list[str]):
    """Send digest to Telegram chats via Bot API."""
    chunks = chunk_message(content, max_len=4096)
    async with httpx.AsyncClient(timeout=30) as client:
        for chat_id in chat_ids:
            for chunk in chunks:
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
                    )
                except Exception as e:
                    logger.error("Telegram delivery failed for %s: %s", chat_id, e)


async def deliver_to_discord(content: str, token: str, channel_ids: list[str]):
    """Send digest to Discord channels via REST API."""
    chunks = chunk_message(content, max_len=2000)
    async with httpx.AsyncClient(timeout=30) as client:
        for channel_id in channel_ids:
            for chunk in chunks:
                try:
                    await client.post(
                        f"https://discord.com/api/v10/channels/{channel_id}/messages",
                        headers={"Authorization": f"Bot {token}"},
                        json={"content": chunk},
                    )
                except Exception as e:
                    logger.error("Discord delivery failed for %s: %s", channel_id, e)


async def deliver_digest(content: str, config):
    """Deliver digest to all configured platforms."""
    delivery = getattr(config, "digest_delivery", "none")
    if delivery == "none":
        return

    if delivery in ("telegram", "both") and config.telegram:
        await deliver_to_telegram(
            content, config.telegram.token, list(config.telegram.allowed_ids),
        )

    if delivery in ("discord", "both") and config.discord:
        await deliver_to_discord(
            content, config.discord.token, list(config.discord.allowed_ids),
        )
