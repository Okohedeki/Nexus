"""Telegram bot — ingests URLs and media into the knowledge graph."""

import logging
import os

from telegram.ext import ApplicationBuilder, MessageHandler, filters

from platforms.telegram.adapter import TelegramContext
from core import commands
from services.knowledge_graph import close_db, init_db

logger = logging.getLogger(__name__)


def _wrap(handler_fn):
    """Create a Telegram handler that wraps Update+Context into TelegramContext."""
    async def wrapper(update, context):
        ctx = TelegramContext(update, context)
        await handler_fn(ctx)
    return wrapper


def main(config):
    """Start the Telegram bot with the given config."""
    logger.info("Telegram bot starting with %d allowed IDs", len(config.telegram.allowed_ids))

    os.makedirs(os.path.dirname(config.kg_db_path), exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(config.kg_db_path), "tmp"), exist_ok=True)

    async def post_init(application):
        application.bot_data["kg_db"] = await init_db(config.kg_db_path)

    async def post_shutdown(application):
        db = application.bot_data.get("kg_db")
        if db:
            await close_db(db)

    app = (
        ApplicationBuilder()
        .token(config.telegram.token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["config"] = config

    # Media: voice, audio, video, video notes → transcribe & ingest
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE,
        _wrap(commands.handle_media_message),
    ))

    # URLs in text → extract & ingest
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Entity("url"),
        _wrap(commands.handle_url_message),
    ))

    # Global error handler
    async def error_handler(update, context):
        logger.error("Unhandled exception: %s", context.error, exc_info=context.error)
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(f"Error: {context.error}")
            except Exception:
                pass

    app.add_error_handler(error_handler)

    logger.info("Telegram polling started")
    app.run_polling()
