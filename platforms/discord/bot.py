"""Discord bot — ingests URLs and media into the knowledge graph."""

import logging
import os
import re

import discord

from platforms.discord.adapter import DiscordMessageContext
from core import commands
from services.knowledge_graph import close_db, init_db

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def main(config):
    """Start the Discord bot with the given config."""
    logger.info("Discord bot starting with %d allowed IDs", len(config.discord.allowed_ids))

    intents = discord.Intents.default()
    intents.message_content = True

    client = discord.Client(intents=intents)

    db = None
    cwd_store: dict[str, str] = {}

    def _ctx(msg):
        return DiscordMessageContext(msg, config, db, cwd_store)

    @client.event
    async def on_ready():
        nonlocal db
        os.makedirs(os.path.dirname(config.kg_db_path), exist_ok=True)
        os.makedirs(os.path.join(os.path.dirname(config.kg_db_path), "tmp"), exist_ok=True)
        db = await init_db(config.kg_db_path)
        logger.info("Discord bot ready as %s", client.user)

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        mc = _ctx(message)

        # Media attachments → transcribe & ingest
        if message.attachments:
            attachment = message.attachments[0]
            content_type = attachment.content_type or ""
            is_media = (
                "audio" in content_type
                or "video" in content_type
                or (attachment.filename and attachment.filename.endswith(
                    (".ogg", ".wav", ".mp3", ".m4a", ".flac", ".mp4", ".webm", ".mov", ".mkv")
                ))
            )
            if is_media:
                await commands.handle_media_message(mc)
                return

        # URLs → extract & ingest
        text = message.content or ""
        if _URL_RE.search(text):
            await commands.handle_url_message(mc)

    client.run(config.discord.token, log_handler=None)
